import sqlite3
import json
import os
from flask import Flask, request, jsonify, render_template, g
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps

app = Flask(__name__)
app.secret_key = 'supersecretkey_for_skfu_hackathon'
CORS(app)

DATABASE = 'museum.db'
ADMIN_PASSWORD = 'admin123'  # общий пароль для сотрудников

# --- Вспомогательные функции БД ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        # Таблицы
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS museums (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address TEXT,
                lat REAL,
                lng REAL,
                description TEXT,
                contacts TEXT,
                photo_url TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS exhibits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                museum_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                photo_url TEXT,
                dating TEXT,
                FOREIGN KEY (museum_id) REFERENCES museums(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                museum_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                date TEXT,
                description TEXT,
                FOREIGN KEY (museum_id) REFERENCES museums(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id TEXT NOT NULL,
                museum_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, museum_id),
                FOREIGN KEY (museum_id) REFERENCES museums(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quiz_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exhibit_id INTEGER NOT NULL,
                question_text TEXT NOT NULL,
                option_a TEXT,
                option_b TEXT,
                option_c TEXT,
                option_d TEXT,
                correct_answer TEXT CHECK (correct_answer IN ('A','B','C','D')),
                FOREIGN KEY (exhibit_id) REFERENCES exhibits(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_quiz_progress (
                user_id TEXT NOT NULL,
                exhibit_id INTEGER NOT NULL,
                completed BOOLEAN DEFAULT 0,
                PRIMARY KEY (user_id, exhibit_id),
                FOREIGN KEY (exhibit_id) REFERENCES exhibits(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                museum_id INTEGER,
                visitor_name TEXT,
                phone TEXT,
                date TEXT,
                time TEXT,
                persons INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        db.commit()

        # Загрузка начальных данных, если таблицы пусты
        cursor.execute("SELECT COUNT(*) FROM museums")
        if cursor.fetchone()[0] == 0:
            load_seed_data(db)
        db.commit()

def load_seed_data(db):
    with open('seed_data.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    cursor = db.cursor()
    for museum in data['museums']:
        cursor.execute('''
            INSERT INTO museums (name, address, lat, lng, description, contacts, photo_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (museum['name'], museum['address'], museum['lat'], museum['lng'],
              museum['description'], museum['contacts'], museum.get('photo_url', '')))
        museum_id = cursor.lastrowid
        # экспонаты
        for ex in museum.get('exhibits', []):
            cursor.execute('''
                INSERT INTO exhibits (museum_id, name, description, photo_url, dating)
                VALUES (?, ?, ?, ?, ?)
            ''', (museum_id, ex['name'], ex['description'], ex.get('photo_url',''), ex.get('dating','')))
            exhibit_id = cursor.lastrowid
            for q in ex.get('questions', []):
                cursor.execute('''
                    INSERT INTO quiz_questions (exhibit_id, question_text, option_a, option_b, option_c, option_d, correct_answer)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (exhibit_id, q['question'], q['a'], q['b'], q['c'], q['d'], q['correct']))
    db.commit()

# --- Декоратор для проверки админ-пароля ---
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        password = request.headers.get('X-Admin-Password')
        if password != ADMIN_PASSWORD:
            return jsonify({'error': 'Unauthorized'}), 403
        return f(*args, **kwargs)
    return decorated

# --- API для посетителей ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/museums')
def get_museums():
    db = get_db()
    museums = db.execute('SELECT * FROM museums').fetchall()
    return jsonify([dict(row) for row in museums])

@app.route('/api/exhibits')
def get_exhibits():
    museum_id = request.args.get('museum_id')
    db = get_db()
    if museum_id:
        exhibits = db.execute('SELECT * FROM exhibits WHERE museum_id = ?', (museum_id,)).fetchall()
    else:
        exhibits = db.execute('SELECT * FROM exhibits').fetchall()
    return jsonify([dict(row) for row in exhibits])

@app.route('/api/events')
def get_events():
    db = get_db()
    events = db.execute('SELECT events.*, museums.name as museum_name FROM events JOIN museums ON events.museum_id = museums.id ORDER BY date DESC').fetchall()
    return jsonify([dict(row) for row in events])

@app.route('/api/subscribe', methods=['POST'])
def subscribe():
    data = request.json
    user_id = data.get('user_id')
    museum_id = data.get('museum_id')
    if not user_id or not museum_id:
        return jsonify({'error': 'Missing user_id or museum_id'}), 400
    db = get_db()
    db.execute('INSERT OR REPLACE INTO subscriptions (user_id, museum_id) VALUES (?, ?)', (user_id, museum_id))
    db.commit()
    return jsonify({'status': 'subscribed'})

@app.route('/api/unsubscribe', methods=['POST'])
def unsubscribe():
    data = request.json
    user_id = data.get('user_id')
    museum_id = data.get('museum_id')
    db = get_db()
    db.execute('DELETE FROM subscriptions WHERE user_id = ? AND museum_id = ?', (user_id, museum_id))
    db.commit()
    return jsonify({'status': 'unsubscribed'})

@app.route('/api/my_news')
def my_news():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify([])
    db = get_db()
    # Получаем подписанные музеи
    subs = db.execute('SELECT museum_id FROM subscriptions WHERE user_id = ?', (user_id,)).fetchall()
    if not subs:
        return jsonify([])
    museum_ids = [row['museum_id'] for row in subs]
    placeholders = ','.join('?' for _ in museum_ids)
    # Новые экспонаты и события из подписанных музеев
    exhibits = db.execute(f'SELECT "exhibit" as type, name, description, photo_url, dating, museum_id, created_at FROM exhibits WHERE museum_id IN ({placeholders})', museum_ids).fetchall()
    events = db.execute(f'SELECT "event" as type, title as name, description, date, museum_id FROM events WHERE museum_id IN ({placeholders})', museum_ids).fetchall()
    news = [dict(row) for row in exhibits] + [dict(row) for row in events]
    news.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return jsonify(news)

@app.route('/api/quiz/questions', methods=['GET'])
def get_quiz_questions():
    exhibit_id = request.args.get('exhibit_id')
    db = get_db()
    questions = db.execute('SELECT * FROM quiz_questions WHERE exhibit_id = ?', (exhibit_id,)).fetchall()
    return jsonify([dict(row) for row in questions])

@app.route('/api/quiz/submit', methods=['POST'])
def submit_quiz():
    data = request.json
    user_id = data.get('user_id')
    exhibit_id = data.get('exhibit_id')
    answers = data.get('answers')  # список словарей {question_id, selected_option}
    if not user_id or not exhibit_id:
        return jsonify({'error': 'Missing data'}), 400
    db = get_db()
    # Проверяем все ответы
    correct = True
    for ans in answers:
        q = db.execute('SELECT correct_answer FROM quiz_questions WHERE id = ?', (ans['question_id'],)).fetchone()
        if not q or q['correct_answer'] != ans['selected_option']:
            correct = False
            break
    if correct:
        # Отмечаем викторину пройденной
        db.execute('INSERT OR REPLACE INTO user_quiz_progress (user_id, exhibit_id, completed) VALUES (?, ?, 1)', (user_id, exhibit_id))
        db.commit()
        # Проверяем, все ли экспонаты пройдены
        total_exhibits = db.execute('SELECT COUNT(*) FROM exhibits').fetchone()[0]
        completed_exhibits = db.execute('SELECT COUNT(*) FROM user_quiz_progress WHERE user_id = ? AND completed = 1', (user_id,)).fetchone()[0]
        all_completed = (total_exhibits == completed_exhibits)
        return jsonify({'success': True, 'all_completed': all_completed})
    else:
        return jsonify({'success': False})

@app.route('/api/quiz/progress', methods=['GET'])
def quiz_progress():
    user_id = request.args.get('user_id')
    db = get_db()
    completed = db.execute('SELECT exhibit_id FROM user_quiz_progress WHERE user_id = ? AND completed = 1', (user_id,)).fetchall()
    return jsonify([row['exhibit_id'] for row in completed])

@app.route('/api/reward', methods=['POST'])
def get_reward():
    user_id = request.json.get('user_id')
    db = get_db()
    total = db.execute('SELECT COUNT(*) FROM exhibits').fetchone()[0]
    completed = db.execute('SELECT COUNT(*) FROM user_quiz_progress WHERE user_id = ? AND completed = 1', (user_id,)).fetchone()[0]
    if total > 0 and completed == total:
        # Генерируем ссылку на картинку-награду
        reward_url = '/static/congrats.png'  # положите картинку в static
        return jsonify({'reward_url': reward_url})
    else:
        return jsonify({'error': 'Not all quizzes completed'}), 400

@app.route('/api/book', methods=['POST'])
def book():
    data = request.json
    db = get_db()
    db.execute('''
        INSERT INTO bookings (user_id, museum_id, visitor_name, phone, date, time, persons)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (data.get('user_id'), data.get('museum_id'), data.get('visitor_name'), data.get('phone'),
          data.get('date'), data.get('time'), data.get('persons')))
    db.commit()
    return jsonify({'status': 'ok'})

# --- API для сотрудников (админ) ---
@app.route('/api/admin/museums', methods=['GET', 'POST', 'PUT', 'DELETE'])
@admin_required
def admin_museums():
    db = get_db()
    if request.method == 'GET':
        museums = db.execute('SELECT * FROM museums').fetchall()
        return jsonify([dict(row) for row in museums])
    elif request.method == 'POST':
        data = request.json
        db.execute('''
            INSERT INTO museums (name, address, lat, lng, description, contacts, photo_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (data['name'], data['address'], data['lat'], data['lng'], data['description'], data.get('contacts'), data.get('photo_url')))
        db.commit()
        return jsonify({'status': 'created', 'id': db.execute('SELECT last_insert_rowid()').fetchone()[0]})
    elif request.method == 'PUT':
        data = request.json
        db.execute('''
            UPDATE museums SET name=?, address=?, lat=?, lng=?, description=?, contacts=?, photo_url=?
            WHERE id=?
        ''', (data['name'], data['address'], data['lat'], data['lng'], data['description'], data.get('contacts'), data.get('photo_url'), data['id']))
        db.commit()
        return jsonify({'status': 'updated'})
    elif request.method == 'DELETE':
        museum_id = request.json.get('id')
        db.execute('DELETE FROM museums WHERE id = ?', (museum_id,))
        db.commit()
        return jsonify({'status': 'deleted'})

@app.route('/api/admin/exhibits', methods=['POST', 'PUT', 'DELETE'])
@admin_required
def admin_exhibits():
    db = get_db()
    if request.method == 'POST':
        data = request.json
        db.execute('''
            INSERT INTO exhibits (museum_id, name, description, photo_url, dating)
            VALUES (?, ?, ?, ?, ?)
        ''', (data['museum_id'], data['name'], data['description'], data.get('photo_url'), data.get('dating')))
        db.commit()
        return jsonify({'status': 'created', 'id': db.execute('SELECT last_insert_rowid()').fetchone()[0]})
    elif request.method == 'PUT':
        data = request.json
        db.execute('''
            UPDATE exhibits SET museum_id=?, name=?, description=?, photo_url=?, dating=?
            WHERE id=?
        ''', (data['museum_id'], data['name'], data['description'], data.get('photo_url'), data.get('dating'), data['id']))
        db.commit()
        return jsonify({'status': 'updated'})
    elif request.method == 'DELETE':
        exhibit_id = request.json.get('id')
        db.execute('DELETE FROM exhibits WHERE id = ?', (exhibit_id,))
        db.commit()
        return jsonify({'status': 'deleted'})

@app.route('/api/admin/events', methods=['POST', 'PUT', 'DELETE'])
@admin_required
def admin_events():
    db = get_db()
    if request.method == 'POST':
        data = request.json
        db.execute('''
            INSERT INTO events (museum_id, title, date, description)
            VALUES (?, ?, ?, ?)
        ''', (data['museum_id'], data['title'], data['date'], data['description']))
        db.commit()
        return jsonify({'status': 'created'})
    elif request.method == 'PUT':
        data = request.json
        db.execute('''
            UPDATE events SET museum_id=?, title=?, date=?, description=?
            WHERE id=?
        ''', (data['museum_id'], data['title'], data['date'], data['description'], data['id']))
        db.commit()
        return jsonify({'status': 'updated'})
    elif request.method == 'DELETE':
        event_id = request.json.get('id')
        db.execute('DELETE FROM events WHERE id = ?', (event_id,))
        db.commit()
        return jsonify({'status': 'deleted'})

@app.route('/api/admin/quiz_questions', methods=['GET', 'POST', 'PUT', 'DELETE'])
@admin_required
def admin_quiz():
    db = get_db()
    if request.method == 'GET':
        questions = db.execute('SELECT * FROM quiz_questions').fetchall()
        return jsonify([dict(row) for row in questions])
    elif request.method == 'POST':
        data = request.json
        db.execute('''
            INSERT INTO quiz_questions (exhibit_id, question_text, option_a, option_b, option_c, option_d, correct_answer)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (data['exhibit_id'], data['question_text'], data['option_a'], data['option_b'], data['option_c'], data['option_d'], data['correct_answer']))
        db.commit()
        return jsonify({'status': 'created'})
    elif request.method == 'PUT':
        data = request.json
        db.execute('''
            UPDATE quiz_questions SET exhibit_id=?, question_text=?, option_a=?, option_b=?, option_c=?, option_d=?, correct_answer=?
            WHERE id=?
        ''', (data['exhibit_id'], data['question_text'], data['option_a'], data['option_b'], data['option_c'], data['option_d'], data['correct_answer'], data['id']))
        db.commit()
        return jsonify({'status': 'updated'})
    elif request.method == 'DELETE':
        q_id = request.json.get('id')
        db.execute('DELETE FROM quiz_questions WHERE id = ?', (q_id,))
        db.commit()
        return jsonify({'status': 'deleted'})

if __name__ == '__main__':
    if not os.path.exists(DATABASE):
        init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
