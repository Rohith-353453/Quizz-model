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

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(32))  # Secure random key for sessions

app.config['MONGO_URI'] = os.getenv('MONGO_URI')
# MongoDB Connection
# Lazy MongoDB Connection (safe for Render)
client = None
db = None

def get_db():
    global client, db
    if client is None:
        mongo_uri = app.config['MONGO_URI']
        client = MongoClient(
            mongo_uri,
            serverSelectionTimeoutMS=30000,
            connectTimeoutMS=30000,
            maxPoolSize=50,
            retryWrites=True
        )
        db = client['flux_db']
    return db
# Flask-Login Setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, username, role):
        self.id = str(id)  # String for session safety
        self.username = username
        self.role = role

@login_manager.user_loader
def load_user(user_id):
    user_data = users.find_one({'_id': ObjectId(user_id)})
    if user_data:
        return User(str(user_data['_id']), user_data['username'], user_data['role'])
    return None

# Routes
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
db = get_db()
users = db['users']
quizzes = db['quizzes']
results = db['results']
    if request.method == 'POST':
        email = request.form['email']
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
db = get_db()
users = db['users']
quizzes = db['quizzes']
results = db['results']
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        role = request.form['role']
        if users.find_one({'email': email}):
            flash('Email exists')
        else:
            user_id = users.insert_one({
                'username': username, 'email': email, 'password': password, 'role': role
            }).inserted_id
            user = User(str(user_id), username, role)
            login_user(user)
            return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', user=current_user)

@app.route('/create_quiz', methods=['POST'])
@login_required
def create_quiz():
db = get_db()
users = db['users']
quizzes = db['quizzes']
results = db['results']
    if current_user.role != 'master':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    title = request.form.get('title', '').strip()
    subject = request.form.get('subject', '').strip()
    duration = int(request.form.get('duration', 0))
    
    if not title or not subject or duration <= 0:
        flash('Please fill title, subject, and duration')
        return redirect(url_for('dashboard'))
    
    # Build questions ENTIRELY from form (NO HARDCODE!)
    questions = []
    for i in range(1, 51):  # Up to 50
        q_text = request.form.get(f'q_text_{i}', '').strip()
        if not q_text:  # Stop at first empty question
            break
        
        q_type = request.form.get(f'q_type_{i}', 'mcq')
        q_answer = request.form.get(f'q_answer_{i}', '').strip()
        q_points = int(request.form.get(f'q_points_{i}', 1))
        
        # Base question dict from form
        q = {
            'type': q_type,
            'text': q_text,
            'answer': q_answer,
            'points': q_points
        }
        
        if q_type == 'mcq':
            options = []
            for j in range(1, 5):  # 4 options
                opt = request.form.get(f'option_{i}_{j}', '').strip()
                if opt:
                    options.append(opt)
            if len(options) < 2:
                flash(f'MCQ Question {i} needs at least 2 options')
                return redirect(url_for('dashboard'))
            q['options'] = options
        elif q_type == 'tf':
            q['answer'] = q_answer.upper()  # Normalize True/False
        # Short answer: Just text/answer (no options)
        
        questions.append(q)
    
    if len(questions) == 0:
        flash('Add at least one question with text')
        return redirect(url_for('dashboard'))
    if len(questions) > 50:
        flash('Max 50 questions')
        return redirect(url_for('dashboard'))
    
    try:
        quiz_id = quizzes.insert_one({
            'title': title,
            'subject': subject,
            'duration': duration,
            'questions': questions,  # ONLY your custom ones!
            'createdBy': ObjectId(current_user.id),
            'date': datetime.now()
        }).inserted_id
        flash(f'Quiz "{title}" created with {len(questions)} questions!')
    except Exception as e:
        flash('Creation failed—try again')
        print(f"DB Error: {e}")  # Check Terminal if needed
    
    return redirect(url_for('quizzes'))  # Redirect to quizzes list after creation

@app.route('/quizzes')
@login_required
def list_quizzes():
db = get_db()
users = db['users']
quizzes = db['quizzes']
results = db['results']
    if current_user.role == 'master':
        quiz_list = list(quizzes.find({'createdBy': ObjectId(current_user.id)}).sort('date', -1))
    else:
        quiz_list = list(quizzes.find({}).sort('date', -1))
    return render_template('quizzes.html', quizzes=quiz_list, user=current_user)

@app.route('/take_quiz/<quiz_id>')
@login_required
def take_quiz(quiz_id):
db = get_db()
users = db['users']
quizzes = db['quizzes']
results = db['results']
    if current_user.role != 'student':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    quiz = quizzes.find_one({'_id': ObjectId(quiz_id)})
    if not quiz:
        flash('Quiz not found')
        return redirect(url_for('dashboard'))
    # Compute seconds as int for safe Jinja
    quiz['duration_seconds'] = int(quiz['duration']) * 60
    return render_template('take_quiz.html', quiz=quiz)

@app.route('/submit_quiz/<quiz_id>', methods=['POST'])
@login_required
def submit_quiz(quiz_id):
db = get_db()
users = db['users']
quizzes = db['quizzes']
results = db['results']
    if current_user.role != 'student':
        return redirect(url_for('dashboard'))
    quiz = quizzes.find_one({'_id': ObjectId(quiz_id)})
    if not quiz:
        flash('Quiz not found')
        return redirect(url_for('dashboard'))
    
    # Parse answers
    answers = {}
    for i, q in enumerate(quiz['questions']):
        q_key = f"q_{i+1}"
        ans = request.form.get(q_key, '').strip()
        answers[q_key] = ans
    
    # Score
    score = 0
    total = sum(q['points'] for q in quiz['questions'])
    scored_answers = []
    for i, q in enumerate(quiz['questions']):
        q_key = f"q_{i+1}"
        ans = answers.get(q_key, '')
        correct = False
        if q['type'] == 'mcq' or q['type'] == 'tf':
            correct = q['answer'] == ans
        elif q['type'] == 'short':
            correct = q['answer'].lower() in ans.lower()  # Basic match
        if correct:
            score += q['points']
        scored_answers.append({
            'question': q['text'], 
            'answer': ans, 
            'correct': correct, 
            'type': q['type']
        })
    
    # Save result
    results.insert_one({
        'user': ObjectId(current_user.id), 
        'quiz': ObjectId(quiz_id),
        'answers': scored_answers, 
        'score': score, 
        'total': total,
        'percentage': (score / total * 100) if total > 0 else 0,
        'date': datetime.now()
    })
    
    return render_template('results.html', score=score, total=total, percentage=(score / total * 100) if total > 0 else 0, answers=scored_answers, quiz_title=quiz['title'])

@app.route('/my_results')
@login_required
def my_results():
db = get_db()
users = db['users']
quizzes = db['quizzes']
results = db['results']
    if current_user.role == 'student':
        res_list = list(results.find({'user': ObjectId(current_user.id)}).sort('date', -1))
    else:
        res_list = list(results.find({}).sort('date', -1))
    return render_template('my_results.html', results=res_list)

@app.route('/leaderboard')
@login_required
def leaderboard():
db = get_db()
users = db['users']
quizzes = db['quizzes']
results = db['results']
    pipeline = [
        {'$group': {'_id': '$user', 'totalScore': {'$sum': '$score'}}},
        {'$sort': {'totalScore': -1}},
        {'$limit': 10}
    ]
    agg_results = list(results.aggregate(pipeline))
    top_users = []
    for agg in agg_results:
        user_data = users.find_one({'_id': agg['_id']})
        top_users.append({
            'username': user_data['username'] if user_data else 'Unknown',
            'score': agg['totalScore']
        })
    return render_template('leaderboard.html', leaderboard=top_users)

@app.route('/edit_quiz/<quiz_id>', methods=['GET', 'POST'])
@login_required
def edit_quiz(quiz_id):
db = get_db()
users = db['users']
quizzes = db['quizzes']
results = db['results']
    if current_user.role != 'master':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
    quiz = quizzes.find_one({'_id': ObjectId(quiz_id), 'createdBy': ObjectId(current_user.id)})
    if not quiz:
        flash('Quiz not found or access denied')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        # Update logic similar to create_quiz
        title = request.form.get('title', '').strip()
        subject = request.form.get('subject', '').strip()
        duration = int(request.form.get('duration', 0))
        
        if not title or not subject or duration <= 0:
            flash('Please fill title, subject, and duration')
            return render_template('edit_quiz.html', quiz=quiz)
        
        # Build updated questions from form (reuse create_quiz logic)
        questions = []
        for i in range(1, 51):
            q_text = request.form.get(f'q_text_{i}', '').strip()
            if not q_text:
                break
            
            q_type = request.form.get(f'q_type_{i}', 'mcq')
            q_answer = request.form.get(f'q_answer_{i}', '').strip()
            q_points = int(request.form.get(f'q_points_{i}', 1))
            
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
            elif q_type == 'tf':
                q['answer'] = q_answer.upper()
            
            questions.append(q)
        
        if len(questions) == 0:
            flash('Add at least one question with text')
            return render_template('edit_quiz.html', quiz=quiz)
        
        try:
            quizzes.update_one(
                {'_id': ObjectId(quiz_id)},
                {'$set': {
                    'title': title,
                    'subject': subject,
                    'duration': duration,
                    'questions': questions,
                    'date': datetime.now()  # Update timestamp
                }}
            )
            flash('Quiz updated successfully!')
            return redirect(url_for('quizzes'))
        except Exception as e:
            flash('Update failed—try again')
            print(f"DB Error: {e}")
        
        return render_template('edit_quiz.html', quiz=quiz)
    
    # Pre-populate form with existing data
    quiz['questions'] = quiz.get('questions', [])  # Ensure list
    return render_template('edit_quiz.html', quiz=quiz)

@app.route('/delete_quiz/<quiz_id>', methods=['GET', 'POST'])  # Accept GET for your current JS
@login_required
def delete_quiz(quiz_id):
db = get_db()
users = db['users']
quizzes = db['quizzes']
results = db['results']
    if current_user.role != 'master':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    
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