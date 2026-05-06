#!/usr/bin/env python3
"""
TAFE NSW Attendance — stdlib only, no pip required.
Run with: python3 app.py
"""
import csv
import json
import os
import webbrowser
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE = Path(__file__).parent
CSV_FILE = BASE / 'attendance.csv'
TEMPLATES = BASE / 'templates'


def today_str():
    return datetime.now().strftime('%d/%m/%Y')


def read_csv():
    if not CSV_FILE.exists():
        return ['Name'], []
    with open(CSV_FILE, newline='', encoding='utf-8') as f:
        rows = list(csv.reader(f))
    if not rows:
        return ['Name'], []
    return rows[0], rows[1:]


def write_csv(headers, data):
    with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(data)


def ensure_today(headers, data):
    today = today_str()
    if today not in headers:
        headers.append(today)
        for row in data:
            while len(row) < len(headers):
                row.append('')
        write_csv(headers, data)
    return headers, data


def api_state():
    headers, data = read_csv()
    headers, data = ensure_today(headers, data)
    today = today_str()
    today_col = headers.index(today)
    students = []
    for i, row in enumerate(data):
        if not row or not row[0].strip():
            continue
        present = len(row) > today_col and row[today_col].strip().upper() == 'P'
        students.append({'index': i, 'name': row[0].strip(), 'present': present})
    students.sort(key=lambda s: s['name'].lower())
    return {'today': today, 'students': students}


def api_sheet():
    headers, data = read_csv()
    students = []
    for row in data:
        if row and row[0].strip():
            padded = row + [''] * (len(headers) - len(row))
            students.append(padded)
    students.sort(key=lambda r: r[0].lower())
    return {'headers': headers, 'students': students}


def api_mark(body):
    idx = int(body['index'])
    headers, data = read_csv()
    headers, data = ensure_today(headers, data)
    today_col = headers.index(today_str())
    row = data[idx]
    while len(row) < len(headers):
        row.append('')
    if row[today_col].strip().upper() == 'P':
        row[today_col] = ''
        present = False
    else:
        row[today_col] = 'P'
        present = True
    write_csv(headers, data)
    return {'present': present, 'index': idx}


def api_add_students(body):
    names = [n.strip() for n in body.get('names', []) if n.strip()]
    if not names:
        return {'ok': False, 'error': 'No names provided'}
    headers, data = read_csv()
    headers, data = ensure_today(headers, data)
    existing = {r[0].strip().lower() for r in data if r and r[0].strip()}
    added = []
    for name in names:
        if name.lower() not in existing:
            row = [name] + [''] * (len(headers) - 1)
            data.append(row)
            existing.add(name.lower())
            added.append(name)
    write_csv(headers, data)
    return {'ok': True, 'added': added}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, path):
        try:
            data = Path(path).read_bytes()
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ('/', '/index.html'):
            self.send_html(TEMPLATES / 'index.html')
        elif path == '/sheet':
            self.send_html(TEMPLATES / 'sheet.html')
        elif path == '/api/state':
            self.send_json(api_state())
        elif path == '/api/sheet':
            self.send_json(api_sheet())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self.read_body()
        if path == '/api/mark':
            self.send_json(api_mark(body))
        elif path == '/api/add':
            self.send_json(api_add_students(body))
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == '__main__':
    port = 5000
    server = HTTPServer(('127.0.0.1', port), Handler)
    url = f'http://localhost:{port}'
    print(f'\n  Attendance running at {url}')
    print('  Press Ctrl+C to stop.\n')
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Stopped.')
