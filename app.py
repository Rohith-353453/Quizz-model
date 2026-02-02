from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from pymongo import MongoClient
from bson import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import os
from datetime import datetime
import secrets

load_dotenv()

app = Flask(__name__, template_folder='templates')
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(32))

app.config['MONGO_URI'] = os.getenv('MONGO_URI')

# Lazy MongoDB Connection
client = None
db = None

def get_db():
    global client, db
    if client is None:
        mongo_uri = app.config['MONGO_URI']
        if not mongo_uri:
            raise ValueError("MONGO_URI not set in environment variables")
        client = MongoClient(
            mongo_uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            maxPoolSize=50,
            retryWrites=True
        )
        db = client['flux_db']
    return db

def get_collections():
    db = get_db()
    return db['users'], db['quizzes'], db['results']

# Flask-Login Setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, username, role):
        self.id = str(id)
        self.username = username
        self.role = role

@login_manager.user_loader
def load_user(user_id):
    try:
        users, _, _ = get_collections()
        user_data = users.find_one({'_id': ObjectId(user_id)})
        if user_data:
            return User(str(user_data['_id']), user_data['username'], user_data['role'])
    except Exception as e:
        print(f"Error loading user: {e}")
        return None
    return None

@app.errorhandler(500)
def internal_server_error(e):
    # Enable error visibility for debugging deployment
    return render_template('500.html', error=e), 500

# Routes
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    users, _, _ = get_collections()
    if request.method == 'POST':
        email = request.form['email'].strip()
        password = request.form['password']
        role = request.form['role']
        user_data = users.find_one({'email': email, 'role': role})
        if user_data and check_password_hash(user_data['password'], password):
            user = User(str(user_data['_id']), user_data['username'], user_data['role'])
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    users, _, _ = get_collections()
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip().lower()
        password = generate_password_hash(request.form['password'])
        role = request.form.get('role', 'student') 
        if role not in ['student', 'master']:
            role = 'student'

        if users.find_one({'email': email}):
            flash('Email already exists')
            return render_template('register.html')
        if users.find_one({'username': username}):
            flash('Username already taken')
            return render_template('register.html')

        user_id = users.insert_one({
            'username': username,
            'email': email,
            'password': password,
            'role': role
        }).inserted_id
        user = User(str(user_id), username, role)
        login_user(user)
        flash('Registration successful!')
        return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', user=current_user)

@app.route('/create_quiz', methods=['GET', 'POST'])
@login_required
def create_quiz():
    if current_user.role != 'master':
        flash('Access denied')
        return redirect(url_for('dashboard'))

    _, quizzes, _ = get_collections()

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        subject = request.form.get('subject', '').strip()
        try:
            duration = int(request.form.get('duration', 0))
        except ValueError:
            flash('Invalid duration')
            return render_template('create_quiz.html')

        if not title or not subject or duration <= 0:
            flash('Please fill title, subject, and a valid duration')
            return render_template('create_quiz.html')

        questions = []
        for i in range(1, 51):
            q_text = request.form.get(f'q_text_{i}', '').strip()
            if not q_text:
                continue

            q_type = request.form.get(f'q_type_{i}', 'mcq')
            q_answer = request.form.get(f'q_answer_{i}', '').strip()
            try:
                q_points = int(request.form.get(f'q_points_{i}', 1))
            except ValueError:
                q_points = 1

            if q_points < 1:
                q_points = 1

            q = {
                'type': q_type,
                'text': q_text,
                'answer': q_answer,
                'points': q_points
            }

            if q_type == 'mcq':
                options = []
                for j in range(1, 5):
                    opt = request.form.get(f'option_{i}_{j}', '').strip()
                    if opt:
                        options.append(opt)
                if len(options) < 2:
                    flash(f'MCQ Question {i} needs at least 2 options')
                    return render_template('create_quiz.html')
                q['options'] = options
            # TF stored as is

            questions.append(q)

        if len(questions) == 0:
            flash('Add at least one question')
            return render_template('create_quiz.html')
        if len(questions) > 50:
            flash('Maximum 50 questions allowed')
            return render_template('create_quiz.html')

        try:
            quiz_id = quizzes.insert_one({
                'title': title,
                'subject': subject,
                'duration': duration,
                'questions': questions,
                'createdBy': ObjectId(current_user.id),
                'date': datetime.now()
            }).inserted_id
            flash(f'Quiz "{title}" created successfully with {len(questions)} questions!')
            return redirect(url_for('quizzes'))
        except Exception as e:
            flash('Quiz creation failed – please try again')
            print(f"DB Error: {e}")

    return render_template('create_quiz.html')

@app.route('/quizzes')
@login_required
def list_quizzes():
    _, quizzes, _ = get_collections()
    if current_user.role == 'master':
        quiz_list = list(quizzes.find({'createdBy': ObjectId(current_user.id)}).sort('date', -1))
    else:
        quiz_list = list(quizzes.find({}).sort('date', -1))
    for quiz in quiz_list:
        quiz['_id'] = str(quiz['_id'])
    return render_template('quizzes.html', quizzes=quiz_list, user=current_user)

@app.route('/take_quiz/<quiz_id>')
@login_required
def take_quiz(quiz_id):
    if current_user.role != 'student':
        flash('Access denied')
        return redirect(url_for('dashboard'))

    _, quizzes, _ = get_collections()
    quiz = quizzes.find_one({'_id': ObjectId(quiz_id)})
    if not quiz:
        flash('Quiz not found')
        return redirect(url_for('quizzes'))

    quiz['duration_seconds'] = int(quiz['duration']) * 60
    quiz['_id'] = str(quiz['_id'])
    return render_template('take_quiz.html', quiz=quiz)

@app.route('/submit_quiz/<quiz_id>', methods=['POST'])
@login_required
def submit_quiz(quiz_id):
    if current_user.role != 'student':
        return redirect(url_for('dashboard'))

    _, quizzes, results = get_collections()
    quiz = quizzes.find_one({'_id': ObjectId(quiz_id)})
    if not quiz:
        flash('Quiz not found')
        return redirect(url_for('quizzes'))

    answers = {}
    for i, q in enumerate(quiz['questions']):
        q_key = f"q_{i+1}"
        ans = request.form.get(q_key, '').strip()
        answers[q_key] = ans

    score = 0
    total = sum(q['points'] for q in quiz['questions'])
    scored_answers = []
    for i, q in enumerate(quiz['questions']):
        q_key = f"q_{i+1}"
        ans = answers.get(q_key, '')
        correct = False
        if q['type'] in ['mcq', 'tf']:
            correct = str(q['answer']).strip() == str(ans).strip()
        elif q['type'] == 'short':
            correct = ans.lower() == q['answer'].lower()
        if correct:
            score += q['points']
        scored_answers.append({
            'question': q['text'],
            'student_answer': ans,
            'correct_answer': q['answer'],
            'correct': correct,
            'type': q['type'],
            'points': q['points']
        })

    percentage = round((score / total * 100), 2) if total > 0 else 0

    results.insert_one({
        'user': ObjectId(current_user.id),
        'username': current_user.username,
        'quiz': ObjectId(quiz_id),
        'answers': scored_answers,
        'score': score,
        'total': total,
        'percentage': percentage,
        'date': datetime.now()
    })

    return render_template('results.html', score=score, total=total, percentage=percentage,
                           answers=scored_answers, quiz_title=quiz['title'])

@app.route('/my_results')
@login_required
def my_results():
    users, quizzes, results = get_collections()

    if current_user.role == 'student':
        raw_results = list(results.find({'user': ObjectId(current_user.id)}).sort('date', -1))
    else:
        raw_results = list(results.find({}).sort('date', -1))

    enriched_results = []
    for res in raw_results:
        quiz = quizzes.find_one({'_id': res['quiz']})
        student = users.find_one({'_id': res['user']})

        enriched_results.append({
            '_id': str(res['_id']),
            'score': res['score'],
            'total': res['total'],
            'percentage': res.get('percentage', 0),
            'date': res['date'],
            'quiz_title': quiz['title'] if quiz else 'Deleted Quiz',
            'quiz_subject': quiz['subject'] if quiz else '',
            'student_name': student['username'] if student else 'Unknown'
        })

    return render_template('my_results.html', results=enriched_results, user=current_user)

@app.route('/leaderboard')
@login_required
def leaderboard():
    users, _, results = get_collections()

    pipeline = [
        {'$match': {'score': {'$exists': True}}},
        {'$group': {'_id': '$user', 'totalScore': {'$sum': '$score'}}},
        {'$sort': {'totalScore': -1}},
        {'$limit': 10}
    ]
    
    try:
        agg_results = list(results.aggregate(pipeline))
    except Exception as e:
        agg_results = []

    top_users = []
    for agg in agg_results:
        if agg['_id']:
            user_data = users.find_one({'_id': ObjectId(agg['_id'])})
            top_users.append({
                'username': user_data['username'] if user_data else 'Unknown Student',
                'score': agg['totalScore']
            })
    return render_template('leaderboard.html', leaderboard=top_users)

@app.route('/edit_quiz/<quiz_id>', methods=['GET', 'POST'])
@login_required
def edit_quiz(quiz_id):
    if current_user.role != 'master':
        flash('Access denied')
        return redirect(url_for('dashboard'))

    _, quizzes, _ = get_collections()
    quiz = quizzes.find_one({'_id': ObjectId(quiz_id), 'createdBy': ObjectId(current_user.id)})
    if not quiz:
        flash('Quiz not found or access denied')
        return redirect(url_for('quizzes'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        subject = request.form.get('subject', '').strip()
        try:
            duration = int(request.form.get('duration', 0))
        except ValueError:
            flash('Invalid duration')
            return render_template('edit_quiz.html', quiz=quiz)

        if not title or not subject or duration <= 0:
            flash('Please fill title, subject, and valid duration')
            return render_template('edit_quiz.html', quiz=quiz)

        questions = []
        for i in range(1, 51):
            q_text = request.form.get(f'q_text_{i}', '').strip()
            if not q_text:
                continue

            q_type = request.form.get(f'q_type_{i}', 'mcq')
            q_answer = request.form.get(f'q_answer_{i}', '').strip()
            try:
                q_points = int(request.form.get(f'q_points_{i}', 1))
            except ValueError:
                q_points = 1
            if q_points < 1:
                q_points = 1

            q = {
                'type': q_type,
                'text': q_text,
                'answer': q_answer,
                'points': q_points
            }

            if q_type == 'mcq':
                options = []
                for j in range(1, 5):
                    opt = request.form.get(f'option_{i}_{j}', '').strip()
                    if opt:
                        options.append(opt)
                if len(options) < 2:
                    flash(f'MCQ Question {i} needs at least 2 options')
                    return render_template('edit_quiz.html', quiz=quiz)
                q['options'] = options
            
            # TF kept as-is

            questions.append(q)

        if len(questions) == 0:
            flash('Add at least one question')
            return render_template('edit_quiz.html', quiz=quiz)

        try:
            quizzes.update_one(
                {'_id': ObjectId(quiz_id)},
                {'$set': {
                    'title': title,
                    'subject': subject,
                    'duration': duration,
                    'questions': questions,
                    'date': datetime.now()
                }}
            )
            flash('Quiz updated successfully!')
            return redirect(url_for('quizzes'))
        except Exception as e:
            flash('Update failed – please try again')
            print(f"DB Error: {e}")

    quiz['_id'] = str(quiz['_id'])
    return render_template('edit_quiz.html', quiz=quiz)

@app.route('/delete_quiz/<quiz_id>', methods=['GET', 'POST'])
@login_required
def delete_quiz(quiz_id):
    if current_user.role != 'master':
        flash('Access denied')
        return redirect(url_for('dashboard'))

    _, quizzes, _ = get_collections()
    quiz = quizzes.find_one({'_id': ObjectId(quiz_id), 'createdBy': ObjectId(current_user.id)})
    if not quiz:
        flash('Quiz not found or access denied')
    else:
        quizzes.delete_one({'_id': ObjectId(quiz_id)})
        flash('Quiz deleted successfully!')
    return redirect(url_for('quizzes'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
