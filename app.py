#!/usr/bin/env python3
"""
Attendance — hosted, multi-teacher edition.

Teachers sign up (gated by SIGNUP_CODE), create classes, and add students.
Each class has a public QR link (/m/<token>) where students self-mark present.
Data lives in Postgres (SQLite fallback for local dev).

Run locally:  SIGNUP_CODE=test SECRET_KEY=dev python app.py
Run in prod:  gunicorn app:app  (see Dockerfile / fly.toml)
"""
import csv
import io
import os
import secrets
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from urllib.parse import quote

from flask import (
    Flask, Response, abort, flash, g, redirect, render_template,
    request, url_for, jsonify,
)
from flask_login import (
    LoginManager, UserMixin, current_user, login_required,
    login_user, logout_user,
)
from flask_wtf import CSRFProtect
import segno
from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint,
    create_engine, event, inspect as sa_inspect, select, text,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, scoped_session,
    sessionmaker,
)
from werkzeug.security import check_password_hash, generate_password_hash


# ── Config ──────────────────────────────────────────────────────────────────
def database_url() -> str:
    """Normalise DATABASE_URL for SQLAlchemy + psycopg3; SQLite fallback."""
    url = os.environ.get('DATABASE_URL')
    if not url:
        return 'sqlite:///local.db'
    # Fly / Heroku hand out postgres:// ; SQLAlchemy wants postgresql+psycopg://
    if url.startswith('postgres://'):
        url = 'postgresql+psycopg://' + url[len('postgres://'):]
    elif url.startswith('postgresql://'):
        url = 'postgresql+psycopg://' + url[len('postgresql://'):]
    return url


SIGNUP_CODE = os.environ.get('SIGNUP_CODE', '')
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', '').strip().lower()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-insecure-change-me')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'


# ── Database ────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


_url = database_url()
_is_sqlite = _url.startswith('sqlite')
_engine_kwargs = {'pool_pre_ping': True, 'future': True}
if _is_sqlite:
    # Allow the connection to be shared across gunicorn threads.
    _engine_kwargs['connect_args'] = {'check_same_thread': False}
engine = create_engine(_url, **_engine_kwargs)

if _is_sqlite:
    @event.listens_for(engine, 'connect')
    def _sqlite_pragmas(dbapi_conn, _rec):
        # WAL lets students check in concurrently without "database is locked";
        # busy_timeout makes writers wait briefly instead of erroring.
        cur = dbapi_conn.cursor()
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA busy_timeout=5000')
        cur.close()

SessionLocal = scoped_session(sessionmaker(bind=engine, future=True))


class Teacher(Base, UserMixin):
    __tablename__ = 'teacher'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # New accounts start without PeopleSoft access; an admin turns it on.
    ps_upload_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    classes: Mapped[list['Klass']] = relationship(
        back_populates='teacher', cascade='all, delete-orphan')


class Klass(Base):
    __tablename__ = 'klass'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey('teacher.id'), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    public_token: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    # Separate from public_token: the QR link is handed to students, but the sync
    # endpoints expose student IDs, so they get their own secret.
    sync_token: Mapped[str | None] = mapped_column(String(32), unique=True)
    self_mark_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    teacher: Mapped[Teacher] = relationship(back_populates='classes')
    students: Mapped[list['Student']] = relationship(
        back_populates='klass', cascade='all, delete-orphan')
    pending: Mapped[list['PendingCheckin']] = relationship(
        back_populates='klass', cascade='all, delete-orphan')


class Student(Base):
    __tablename__ = 'student'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey('klass.id'), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    emplid: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    klass: Mapped[Klass] = relationship(back_populates='students')
    attendance: Mapped[list['Attendance']] = relationship(
        back_populates='student', cascade='all, delete-orphan')
    aliases: Mapped[list['Alias']] = relationship(
        back_populates='student', cascade='all, delete-orphan')


class Attendance(Base):
    __tablename__ = 'attendance'
    __table_args__ = (UniqueConstraint('student_id', 'day', name='uq_student_day'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('student.id'), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    marked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    student: Mapped[Student] = relationship(back_populates='attendance')


class Alias(Base):
    """An alternate name that resolves to a student (e.g. 'Rob' -> 'Robert Smith')."""
    __tablename__ = 'alias'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('student.id'), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    student: Mapped[Student] = relationship(back_populates='aliases')


class PendingCheckin(Base):
    """A self check-in whose typed name matched no student — awaits teacher action."""
    __tablename__ = 'pending_checkin'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey('klass.id'), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    klass: Mapped[Klass] = relationship(back_populates='pending')


Base.metadata.create_all(engine)


def add_missing_columns() -> None:
    """create_all() only creates missing tables, never alters existing ones."""
    added = {'klass': [('sync_token', 'VARCHAR(32)')],
             'student': [('emplid', 'VARCHAR(20)')],
             # Accounts predating this column keep PeopleSoft access; only
             # accounts created afterwards start disabled.
             'teacher': [('is_admin', 'BOOLEAN DEFAULT 0 NOT NULL'),
                         ('ps_upload_enabled', 'BOOLEAN DEFAULT 1 NOT NULL')]}
    insp = sa_inspect(engine)
    with engine.begin() as conn:
        for table, columns in added.items():
            if not insp.has_table(table):
                continue
            existing = {c['name'] for c in insp.get_columns(table)}
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))


def grant_admin_from_env() -> None:
    """ADMIN_EMAIL is how the first admin gets made — there's no bootstrap UI."""
    if not ADMIN_EMAIL:
        return
    with engine.begin() as conn:
        conn.execute(text('UPDATE teacher SET is_admin = :yes WHERE lower(email) = :email'),
                     {'yes': True, 'email': ADMIN_EMAIL})


add_missing_columns()
grant_admin_from_env()


@app.teardown_appcontext
def remove_session(exc=None):
    SessionLocal.remove()


@login_manager.user_loader
def load_user(user_id: str):
    return SessionLocal.get(Teacher, int(user_id))


# ── Helpers ─────────────────────────────────────────────────────────────────
def fmt_date(d: date) -> str:
    return d.strftime('%d/%m/%Y')


def new_token() -> str:
    return secrets.token_urlsafe(12)


def get_owned_class(class_id: int) -> Klass:
    """Fetch a class the current teacher owns, or 404."""
    klass = SessionLocal.get(Klass, class_id)
    if klass is None or klass.teacher_id != current_user.id:
        abort(404)
    return klass


def get_class_by_token(token: str) -> Klass:
    klass = SessionLocal.scalar(
        select(Klass).where(Klass.public_token == token))
    if klass is None:
        abort(404)
    return klass


def present_ids(class_id: int, day: date) -> set[int]:
    rows = SessionLocal.scalars(
        select(Attendance.student_id)
        .join(Student, Student.id == Attendance.student_id)
        .where(Student.class_id == class_id, Attendance.day == day)
    ).all()
    return set(rows)


def sorted_students(klass: Klass) -> list[Student]:
    return sorted(klass.students, key=lambda s: s.name.lower())


def _get_student(klass: Klass, student_id: int) -> Student:
    student = SessionLocal.get(Student, student_id)
    if student is None or student.class_id != klass.id:
        abort(404)
    return student


def toggle_mark(klass: Klass, student_id: int, day: date) -> bool:
    """Toggle presence for a student on a day. Returns new present state.
    Teacher-only — students use set_present (one-way)."""
    _get_student(klass, student_id)
    existing = SessionLocal.scalar(
        select(Attendance).where(
            Attendance.student_id == student_id, Attendance.day == day))
    if existing:
        SessionLocal.delete(existing)
        SessionLocal.commit()
        return False
    SessionLocal.add(Attendance(student_id=student_id, day=day))
    SessionLocal.commit()
    return True


def set_present(klass: Klass, student_id: int, day: date) -> bool:
    """Mark a student present for a day. Never un-marks (public self check-in)."""
    _get_student(klass, student_id)
    existing = SessionLocal.scalar(
        select(Attendance).where(
            Attendance.student_id == student_id, Attendance.day == day))
    if not existing:
        SessionLocal.add(Attendance(student_id=student_id, day=day))
        SessionLocal.commit()
    return True


def find_student(klass: Klass, typed: str) -> Student | None:
    """Resolve a typed name to a student by exact name or alias (case-insensitive)."""
    key = typed.strip().lower()
    if not key:
        return None
    for s in klass.students:
        if s.name.strip().lower() == key:
            return s
    for s in klass.students:
        if any(a.name.strip().lower() == key for a in s.aliases):
            return s
    return None


def flip_name(typed: str) -> str:
    """PeopleSoft renders names as 'Surname,Given' — flip to match our stored form."""
    if ',' not in typed:
        return typed
    surname, _, given = typed.partition(',')
    return f'{given.strip()} {surname.strip()}'.strip()


def name_index(klass: Klass) -> dict[str, Student]:
    """Every name and alias in the class, lowercased, for exact lookup."""
    index: dict[str, Student] = {}
    for s in klass.students:
        index.setdefault(s.name.strip().lower(), s)
        for a in s.aliases:
            index.setdefault(a.name.strip().lower(), s)
    return index


def match_roster_row(index: dict[str, Student], cells: list[str]) -> Student | None:
    """Find the student a PeopleSoft grid row refers to.

    The roster splits Surname and First Name into separate columns, so besides
    trying each cell whole we also try joining pairs of them in both orders.
    """
    for cell in cells:
        for candidate in (cell, flip_name(cell)):
            found = index.get(candidate.strip().lower())
            if found:
                return found
    for surname in cells:
        for given in cells:
            if surname is given:
                continue
            found = index.get(f'{given} {surname}'.strip().lower())
            if found:
                return found
    return None


def get_class_by_sync_token(token: str) -> Klass:
    klass = SessionLocal.scalar(select(Klass).where(Klass.sync_token == token))
    if klass is None:
        abort(404)
    if not klass.teacher.ps_upload_enabled:
        abort(403)
    return klass


def ensure_sync_token(klass: Klass) -> str:
    if not klass.sync_token:
        klass.sync_token = new_token()
        SessionLocal.commit()
    return klass.sync_token


def add_names(klass: Klass, names: list[str]) -> list[str]:
    """Add de-duplicated names to a class. Returns names actually added."""
    existing = {s.name.strip().lower() for s in klass.students}
    added = []
    for raw in names:
        name = raw.strip()
        if name and name.lower() not in existing:
            SessionLocal.add(Student(class_id=klass.id, name=name))
            existing.add(name.lower())
            added.append(name)
    if added:
        SessionLocal.commit()
    return added


def state_payload(klass: Klass) -> dict:
    today = date.today()
    marked = present_ids(klass.id, today)
    students = [
        {'id': s.id, 'name': s.name, 'present': s.id in marked}
        for s in sorted_students(klass)
    ]
    return {'today': fmt_date(today), 'students': students}


def sheet_payload(klass: Klass) -> dict:
    """Pivot attendance into wide format: Name + one column per date."""
    days = SessionLocal.scalars(
        select(Attendance.day)
        .join(Student, Student.id == Attendance.student_id)
        .where(Student.class_id == klass.id)
        .distinct()
    ).all()
    days = sorted(set(days))
    headers = ['Name'] + [fmt_date(d) for d in days]
    marked_by_day = {d: present_ids(klass.id, d) for d in days}
    rows = []
    for s in sorted_students(klass):
        row = [s.name] + ['P' if s.id in marked_by_day[d] else '' for d in days]
        rows.append(row)
    return {'headers': headers, 'rows': rows}


# ── Auth routes ─────────────────────────────────────────────────────────────
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        code = request.form.get('code', '')
        if not SIGNUP_CODE or code != SIGNUP_CODE:
            flash('Invalid signup code.', 'warn')
        elif not email or not password:
            flash('Email and password are required.', 'warn')
        elif len(password) < 8:
            flash('Password must be at least 8 characters.', 'warn')
        elif SessionLocal.scalar(select(Teacher).where(Teacher.email == email)):
            flash('An account with that email already exists.', 'warn')
        else:
            teacher = Teacher(
                email=email, password_hash=generate_password_hash(password))
            SessionLocal.add(teacher)
            SessionLocal.commit()
            login_user(teacher)
            return redirect(url_for('dashboard'))
    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        teacher = SessionLocal.scalar(
            select(Teacher).where(Teacher.email == email))
        if teacher and check_password_hash(teacher.password_hash, password):
            login_user(teacher)
            return redirect(url_for('dashboard'))
        flash('Incorrect email or password.', 'warn')
    return render_template('login.html')


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ── Teacher routes ──────────────────────────────────────────────────────────
@app.route('/')
@login_required
def dashboard():
    classes = sorted(current_user.classes, key=lambda k: k.name.lower())
    return render_template('dashboard.html', classes=classes)


@app.route('/classes', methods=['POST'])
@login_required
def create_class():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Class name is required.', 'warn')
        return redirect(url_for('dashboard'))
    klass = Klass(teacher_id=current_user.id, name=name, public_token=new_token())
    SessionLocal.add(klass)
    SessionLocal.commit()
    return redirect(url_for('view_class', class_id=klass.id))


@app.route('/classes/<int:class_id>')
@login_required
def view_class(class_id):
    klass = get_owned_class(class_id)
    pending = sorted(klass.pending, key=lambda p: (p.day, p.name.lower()))
    return render_template('class.html', klass=klass, pending=pending,
                           **state_payload(klass))


@app.route('/classes/<int:class_id>/manage')
@login_required
def manage_class(class_id):
    klass = get_owned_class(class_id)
    return render_template('manage.html', klass=klass, students=sorted_students(klass))


@app.route('/classes/<int:class_id>/students', methods=['POST'])
@login_required
def add_students(class_id):
    klass = get_owned_class(class_id)
    raw = request.form.get('names', '')
    added = add_names(klass, raw.splitlines())
    flash(f'Added {len(added)} student{"s" if len(added) != 1 else ""}.', 'ok')
    return redirect(url_for('view_class', class_id=class_id))


@app.route('/classes/<int:class_id>/students/<int:sid>/rename', methods=['POST'])
@login_required
def rename_student(class_id, sid):
    klass = get_owned_class(class_id)
    student = _get_student(klass, sid)
    name = request.form.get('name', '').strip()
    if name:
        student.name = name
        SessionLocal.commit()
        flash('Renamed to ' + name + '.', 'ok')
    return redirect(url_for('manage_class', class_id=class_id))


@app.route('/classes/<int:class_id>/students/<int:sid>/delete', methods=['POST'])
@login_required
def delete_student(class_id, sid):
    klass = get_owned_class(class_id)
    student = _get_student(klass, sid)
    name = student.name
    SessionLocal.delete(student)
    SessionLocal.commit()
    flash('Deleted ' + name + '.', 'ok')
    return redirect(url_for('manage_class', class_id=class_id))


@app.route('/classes/<int:class_id>/aliases/<int:aid>/delete', methods=['POST'])
@login_required
def delete_alias(class_id, aid):
    klass = get_owned_class(class_id)
    alias = SessionLocal.get(Alias, aid)
    if alias is None or alias.student.class_id != klass.id:
        abort(404)
    SessionLocal.delete(alias)
    SessionLocal.commit()
    flash('Alias removed.', 'ok')
    return redirect(url_for('manage_class', class_id=class_id))


@app.route('/classes/<int:class_id>/students/<int:sid>/alias', methods=['POST'])
@login_required
def add_alias(class_id, sid):
    klass = get_owned_class(class_id)
    student = _get_student(klass, sid)
    name = (request.form.get('name') or '').strip()
    existing = find_student(klass, name) if name else None

    if not name:
        flash('Enter an alias.', 'warn')
    elif existing is student:
        flash(f'{name} already checks in {student.name}.', 'warn')
    elif existing is not None:
        flash(f'{name} already checks in {existing.name}.', 'warn')
    else:
        SessionLocal.add(Alias(student_id=student.id, name=name))
        SessionLocal.commit()
        flash(f'{name} now checks in {student.name}.', 'ok')
    return redirect(url_for('manage_class', class_id=class_id))


def _get_pending(klass: Klass, pid: int) -> PendingCheckin:
    pc = SessionLocal.get(PendingCheckin, pid)
    if pc is None or pc.class_id != klass.id:
        abort(404)
    return pc


@app.route('/classes/<int:class_id>/pending/<int:pid>/create', methods=['POST'])
@login_required
def pending_create(class_id, pid):
    klass = get_owned_class(class_id)
    pc = _get_pending(klass, pid)
    name = request.form.get('name', '').strip() or pc.name
    day = pc.day
    student = next((s for s in klass.students
                    if s.name.strip().lower() == name.lower()), None)
    if student is None:
        student = Student(class_id=klass.id, name=name)
        SessionLocal.add(student)
        SessionLocal.flush()
    set_present(klass, student.id, day)
    SessionLocal.delete(pc)
    SessionLocal.commit()
    flash('Added ' + name + ' and marked present.', 'ok')
    return redirect(url_for('view_class', class_id=class_id))


@app.route('/classes/<int:class_id>/pending/<int:pid>/alias', methods=['POST'])
@login_required
def pending_alias(class_id, pid):
    klass = get_owned_class(class_id)
    pc = _get_pending(klass, pid)
    student = _get_student(klass, int(request.form.get('student_id', 0) or 0))
    typed, day = pc.name, pc.day
    if not any(a.name.strip().lower() == typed.strip().lower() for a in student.aliases):
        SessionLocal.add(Alias(student_id=student.id, name=typed))
    set_present(klass, student.id, day)
    SessionLocal.delete(pc)
    SessionLocal.commit()
    flash('"' + typed + '" linked to ' + student.name + ' and marked present.', 'ok')
    return redirect(url_for('view_class', class_id=class_id))


@app.route('/classes/<int:class_id>/pending/<int:pid>/dismiss', methods=['POST'])
@login_required
def pending_dismiss(class_id, pid):
    klass = get_owned_class(class_id)
    pc = _get_pending(klass, pid)
    SessionLocal.delete(pc)
    SessionLocal.commit()
    flash('Dismissed check-in.', 'ok')
    return redirect(url_for('view_class', class_id=class_id))


@app.route('/classes/<int:class_id>/share')
@login_required
def share_class(class_id):
    klass = get_owned_class(class_id)
    link = url_for('mark_page', token=klass.public_token, _external=True)
    qr_svg = segno.make(link, error='m').svg_inline(scale=6, dark='#0f1117',
                                                     light='#ffffff')
    return render_template('share.html', klass=klass, link=link, qr_svg=qr_svg)


@app.route('/classes/<int:class_id>/self-mark', methods=['POST'])
@login_required
def toggle_self_mark(class_id):
    klass = get_owned_class(class_id)
    klass.self_mark_enabled = not klass.self_mark_enabled
    SessionLocal.commit()
    flash('Self check-in ' + ('enabled.' if klass.self_mark_enabled else 'disabled.'),
          'ok')
    return redirect(url_for('view_class', class_id=class_id))


@app.route('/classes/<int:class_id>/regenerate-token', methods=['POST'])
@login_required
def regenerate_token(class_id):
    klass = get_owned_class(class_id)
    klass.public_token = new_token()
    SessionLocal.commit()
    flash('New QR link generated — the old one no longer works.', 'ok')
    return redirect(url_for('share_class', class_id=class_id))


@app.route('/classes/<int:class_id>/sheet')
@login_required
def class_sheet(class_id):
    klass = get_owned_class(class_id)
    return render_template('sheet.html', klass=klass, **sheet_payload(klass))


@app.route('/classes/<int:class_id>/export.csv')
@login_required
def export_csv(class_id):
    klass = get_owned_class(class_id)
    data = sheet_payload(klass)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(data['headers'])
    writer.writerows(data['rows'])
    safe = ''.join(c if c.isalnum() else '_' for c in klass.name) or 'attendance'
    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={safe}.csv'},
    )


@app.route('/classes/<int:class_id>/delete', methods=['POST'])
@login_required
def delete_class(class_id):
    klass = get_owned_class(class_id)
    SessionLocal.delete(klass)
    SessionLocal.commit()
    flash('Class deleted.', 'ok')
    return redirect(url_for('dashboard'))


# ── Teacher kiosk API (session-authenticated) ───────────────────────────────
@app.route('/api/classes/<int:class_id>/state')
@login_required
def api_class_state(class_id):
    klass = get_owned_class(class_id)
    return jsonify(state_payload(klass))


@app.route('/api/classes/<int:class_id>/mark', methods=['POST'])
@login_required
def api_class_mark(class_id):
    klass = get_owned_class(class_id)
    body = request.get_json(silent=True) or {}
    present = toggle_mark(klass, int(body['id']), date.today())
    return jsonify({'id': body['id'], 'present': present})


# ── Public student self check-in (token-scoped, no login) ────────────────────
@app.route('/m/<token>')
def mark_page(token):
    klass = get_class_by_token(token)
    return render_template('mark.html', klass=klass, token=token,
                           today=fmt_date(date.today()))


@app.route('/api/m/<token>/checkin', methods=['POST'])
@csrf.exempt
def api_public_checkin(token):
    """Student types their name. Match -> present. No match -> pending for teacher."""
    klass = get_class_by_token(token)
    if not klass.self_mark_enabled:
        return jsonify({'ok': False, 'error': 'Check-in is closed.'}), 403
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Please enter your name.'}), 400

    today = date.today()
    student = find_student(klass, name)
    if student:
        already = SessionLocal.scalar(select(Attendance).where(
            Attendance.student_id == student.id, Attendance.day == today)) is not None
        set_present(klass, student.id, today)
        return jsonify({'ok': True, 'status': 'already' if already else 'present',
                        'name': student.name})

    # Unmatched — record as pending (dedupe same name + day) for the teacher to resolve.
    key = name.lower()
    if not any(p.day == today and p.name.strip().lower() == key for p in klass.pending):
        SessionLocal.add(PendingCheckin(class_id=klass.id, name=name, day=today))
        SessionLocal.commit()
    return jsonify({'ok': True, 'status': 'pending', 'name': name})


# ── Admin ────────────────────────────────────────────────────────────────────
def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def get_teacher(teacher_id: int) -> Teacher:
    teacher = SessionLocal.get(Teacher, teacher_id)
    if teacher is None:
        abort(404)
    return teacher


@app.route('/admin')
@admin_required
def admin_page():
    teachers = SessionLocal.scalars(select(Teacher).order_by(Teacher.created_at)).all()
    rows = []
    for t in teachers:
        students = sum(len(k.students) for k in t.classes)
        rows.append({'teacher': t, 'classes': len(t.classes), 'students': students})
    return render_template('admin.html', rows=rows, admin_email=ADMIN_EMAIL)


@app.route('/admin/teachers/<int:teacher_id>/peoplesoft', methods=['POST'])
@admin_required
def admin_toggle_peoplesoft(teacher_id):
    teacher = get_teacher(teacher_id)
    teacher.ps_upload_enabled = not teacher.ps_upload_enabled
    SessionLocal.commit()
    state = 'enabled' if teacher.ps_upload_enabled else 'disabled'
    flash(f'PeopleSoft upload {state} for {teacher.email}.', 'ok')
    return redirect(url_for('admin_page'))


@app.route('/admin/teachers/<int:teacher_id>/admin', methods=['POST'])
@admin_required
def admin_toggle_admin(teacher_id):
    teacher = get_teacher(teacher_id)
    if teacher.id == current_user.id:
        flash("You can't remove your own admin access.", 'error')
    elif teacher.email.strip().lower() == ADMIN_EMAIL:
        flash(f'{teacher.email} is the ADMIN_EMAIL and stays an admin.', 'error')
    else:
        teacher.is_admin = not teacher.is_admin
        SessionLocal.commit()
        state = 'now an admin' if teacher.is_admin else 'no longer an admin'
        flash(f'{teacher.email} is {state}.', 'ok')
    return redirect(url_for('admin_page'))


@app.route('/admin/teachers/<int:teacher_id>/delete', methods=['POST'])
@admin_required
def admin_delete_teacher(teacher_id):
    teacher = get_teacher(teacher_id)
    if teacher.id == current_user.id:
        flash("You can't delete your own account.", 'error')
        return redirect(url_for('admin_page'))
    if request.form.get('confirm', '').strip().lower() != teacher.email.strip().lower():
        flash('Type the account\'s email exactly to confirm deletion.', 'error')
        return redirect(url_for('admin_page'))

    email, classes = teacher.email, len(teacher.classes)
    SessionLocal.delete(teacher)
    SessionLocal.commit()
    flash(f'Deleted {email} and {classes} class(es), including all attendance.', 'ok')
    return redirect(url_for('admin_page'))


# ── PeopleSoft sync (bookmarklet running on the TAFE roster page) ────────────
PS_ORIGIN = 'https://staff-campus.oci.tafensw.edu.au'
PS_WORKCENTRE = PS_ORIGIN + '/psp/pdcmp/EMPLOYEE/SA/c/RX_MENU.RX_AT_WORKAREA.GBL'


@app.after_request
def sync_cors(resp):
    """The bookmarklet runs on the TAFE origin, so these routes must allow it."""
    if request.path.startswith('/api/sync/'):
        resp.headers['Access-Control-Allow-Origin'] = PS_ORIGIN
        resp.headers['Vary'] = 'Origin'
    return resp


def bookmarklet_for(bridge_url: str) -> str:
    """Inline the sync script into a javascript: URL — CSP blocks loading it remotely."""
    src = (Path(app.static_folder) / 'ps_sync.js').read_text()
    body = ' '.join(line.strip() for line in src.splitlines() if line.strip())
    return 'javascript:' + quote(body.replace('__BRIDGE__', bridge_url),
                                 safe="!$&'()*+,-./:;=?@_~")


@app.route('/sync/<token>/bridge')
def sync_bridge(token):
    """Same-origin helper window the bookmarklet talks to over postMessage."""
    get_class_by_sync_token(token)
    return render_template('bridge.html', token=token, ps_origin=PS_ORIGIN)


@app.route('/classes/<int:class_id>/sync')
@login_required
def sync_page(class_id):
    klass = get_owned_class(class_id)
    if not current_user.ps_upload_enabled:
        return render_template('sync_disabled.html', klass=klass), 403
    bridge = url_for('sync_bridge', token=ensure_sync_token(klass), _external=True)
    missing = [s.name for s in sorted_students(klass) if not s.emplid]

    # The list for a chosen day, so there's a copy-paste path that doesn't
    # depend on the popup reaching this app at all.
    try:
        day = date.fromisoformat(request.args.get('date', ''))
    except ValueError:
        day = date.today()
    marked = present_ids(klass.id, day)
    students = sorted_students(klass)
    return render_template(
        'sync.html', klass=klass, missing=missing, bookmarklet=bookmarklet_for(bridge),
        day=day, workcentre_url=PS_WORKCENTRE,
        present_ids_list=[s.emplid for s in students if s.id in marked and s.emplid],
        present_no_id=[s.name for s in students if s.id in marked and not s.emplid])


@app.route('/api/sync/<token>/roster', methods=['POST'])
@csrf.exempt
def api_sync_roster(token):
    """Learn EMPLID -> student from the roster grid the bookmarklet scraped."""
    klass = get_class_by_sync_token(token)
    body = request.get_json(force=True, silent=True) or {}

    index = name_index(klass)
    learned, unknown, conflicts = 0, [], []
    for row in body.get('rows', []):
        emplid = str(row.get('emplid') or '').strip()
        if not emplid:
            continue
        student = match_roster_row(index, [str(c) for c in row.get('cells', [])])
        if student is None:
            unknown.append(emplid)
        elif student.emplid is None:
            student.emplid = emplid
            learned += 1
        elif student.emplid != emplid:
            conflicts.append(student.name)
    SessionLocal.commit()
    return jsonify({'ok': True, 'learned': learned,
                    'unknown': unknown, 'conflicts': conflicts})


@app.route('/api/sync/<token>/present')
def api_sync_present(token):
    """The EMPLIDs marked present on a given day."""
    klass = get_class_by_sync_token(token)
    try:
        day = date.fromisoformat(request.args.get('date', ''))
    except ValueError:
        return jsonify({'ok': False, 'error': 'date must be YYYY-MM-DD'}), 400

    marked = present_ids(klass.id, day)
    emplids = [s.emplid for s in klass.students if s.id in marked and s.emplid]
    missing = [s.name for s in sorted_students(klass) if s.id in marked and not s.emplid]
    return jsonify({'date': day.isoformat(), 'emplids': emplids,
                    'missing_emplid': missing})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='127.0.0.1', port=port, debug=True)
