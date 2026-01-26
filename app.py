from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from pymongo import MongoClient
from bson import ObjectId
from bson.errors import InvalidId
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
        client = MongoClient(
            mongo_uri,
            serverSelectionTimeoutMS=30000,
            connectTimeoutMS=30000,
            maxPoolSize=50,
            retryWrites=True
        )
        db = client['flux_db']
    return db

def get_collections():
    db = get_db()
    return db['users'], db['quizzes'], db['results']

def safe_object_id(id_str):
    if not id_str:
        return None
    try:
        return ObjectId(id_str)
    except InvalidId:
        return None

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
            return User(user_data['_id'], user_data['username'], user_data['role'])
    except InvalidId:
        pass
    return None

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
        user_data = users.find_one({'email': email})
        if user_data and check_password_hash(user_data['password'], password):
            user = User(user_data['_id'], user_data['username'], user_data['role'])
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
        role = 'student'  # Hardcoded – masters must be created manually in DB

        if users.find_one({'email': email}):
            flash('Email already exists')
        else:
            user_id = users.insert_one({
                'username': username,
                'email': email,
                'password': password,
                'role': role
            }).inserted_id
            user = User(user_id, username, role)
            login_user(user)
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

    users, quizzes, results = get_collections()

    if request.method == 'GET':
        return render_template('create_quiz.html')

    title = request.form.get('title', '').strip()
    subject = request.form.get('subject', '').strip()
    duration_str = request.form.get('duration', '0')

    try:
        duration = int(duration_str)
        if duration <= 0:
            raise ValueError
    except ValueError:
        flash('Invalid duration (must be positive integer)')
        return redirect(url_for('create_quiz'))

    if not title or not subject:
        flash('Title and subject are required')
        return redirect(url_for('create_quiz'))

    questions = []
    i = 1
    while True:
        q_text = request.form.get(f'q_text_{i}', '').strip()
        if not q_text:
            break

        q_type = request.form.get(f'q_type_{i}', 'mcq')
        q_answer = request.form.get(f'q_answer_{i}', '').strip()
        q_points_str = request.form.get(f'q_points_{i}', '1')

        if not q_answer:
            flash(f'Question {i}: Answer is required')
            return redirect(url_for('create_quiz'))

        try:
            q_points = int(q_points_str)
            if q_points <= 0:
                raise ValueError
        except ValueError:
            flash(f'Question {i}: Invalid points')
            return redirect(url_for('create_quiz'))

        q = {
            'type': q_type,
            'text': q_text,
            'answer': q_answer,
            'points': q_points
        }

        if q_type == 'mcq':
            options = [request.form.get(f'option_{i}_{j}', '').strip() for j in range(1, 5)]
            options = [opt for opt in options if opt]  # remove empty
            if len(options) < 2:
                flash(f'Question {i}: MCQ needs at least 2 options')
                return redirect(url_for('create_quiz'))
            if q_answer not in options:
                flash(f'Question {i}: Correct answer must be one of the options')
                return redirect(url_for('create_quiz'))
            q['options'] = options

        elif q_type == 'tf':
            normalized = q_answer.upper()
            if normalized not in ['TRUE', 'FALSE']:
                flash(f'Question {i}: True/False answer must be True or False')
                return redirect(url_for('create_quiz'))
            q['answer'] = normalized

        questions.append(q)
        i += 1

        if i > 50:
            flash('Maximum 50 questions allowed')
            return redirect(url_for('create_quiz'))

    if len(questions) == 0:
        flash('At least one question required')
        return redirect(url_for('create_quiz'))

    try:
        quizzes.insert_one({
            'title': title,
            'subject': subject,
            'duration': duration,
            'questions': questions,
            'createdBy': ObjectId(current_user.id),
            'date': datetime.now()
        })
        flash(f'Quiz "{title}" created successfully!')
    except Exception as e:
        flash('Quiz creation failed')
        print(f"DB Error: {e}")

    return redirect(url_for('quizzes'))

@app.route('/quizzes', endpoint='quizzes')  # ← Added endpoint here
@login_required
def list_quizzes():
    users, quizzes, results = get_collections()
    if current_user.role == 'master':
        quiz_list = list(quizzes.find({'createdBy': ObjectId(current_user.id)}).sort('date', -1))
    else:
        quiz_list = list(quizzes.find({}).sort('date', -1))
    return render_template('quizzes.html', quizzes=quiz_list, user=current_user)

@app.route('/take_quiz/<quiz_id>')
@login_required
def take_quiz(quiz_id):
    if current_user.role != 'student':
        flash('Access denied')
        return redirect(url_for('dashboard'))

    users, quizzes, results = get_collections()
    quiz_oid = safe_object_id(quiz_id)
    if not quiz_oid:
        flash('Invalid quiz ID')
        return redirect(url_for('quizzes'))

    quiz = quizzes.find_one({'_id': quiz_oid})
    if not quiz:
        flash('Quiz not found')
        return redirect(url_for('quizzes'))

    existing = results.find_one({'user': ObjectId(current_user.id), 'quiz': quiz_oid})
    if existing:
        flash('You have already taken this quiz')
        return redirect(url_for('my_results'))

    quiz['duration_seconds'] = int(quiz['duration']) * 60
    return render_template('take_quiz.html', quiz=quiz)

@app.route('/submit_quiz/<quiz_id>', methods=['POST'])
@login_required
def submit_quiz(quiz_id):
    if current_user.role != 'student':
        return redirect(url_for('dashboard'))
    
    users, quizzes, results = get_collections()
    quiz_oid = safe_object_id(quiz_id)
    if not quiz_oid:
        flash('Invalid quiz ID')
        return redirect(url_for('quizzes'))
    
    quiz = quizzes.find_one({'_id': quiz_oid})
    if not quiz:
        flash('Quiz not found')
        return redirect(url_for('quizzes'))
    
    existing = results.find_one({'user': ObjectId(current_user.id), 'quiz': quiz_oid})
    if existing:
        flash('You have already submitted this quiz')
        return redirect(url_for('my_results'))
    
    # Debug: Start of submission
    username = current_user.username if hasattr(current_user, 'username') else 'Unknown'
    print(f"\n=== QUIZ SUBMISSION START ===\nUser: {username} (ID: {current_user.id})\nQuiz ID: {quiz_id} | Title: {quiz.get('title', 'N/A')}\nNumber of questions: {len(quiz['questions'])}\n")
    
    answers = {}
    for i, q in enumerate(quiz['questions']):
        q_key = f"q_{i+1}"
        ans = request.form.get(q_key, '').strip()
        answers[q_key] = ans
        
        # Debug per question input
        print(f"Raw form data for question {i+1} (key: {q_key}): User answer = '{ans}'")
    
    score = 0
    total = sum(q['points'] for q in quiz['questions'])
    scored_answers = []
    
    print(f"\n--- SCORE CALCULATION ---\nCalculated total possible points: {total}\n")
    
    for i, q in enumerate(quiz['questions']):
        q_key = f"q_{i+1}"
        ans = answers.get(q_key, '')
        correct = False
        
        # Debug: Show question details
        print(f"Question {i+1}:")
        print(f"  Text: {q['text']}")
        print(f"  Type: {q['type']}")
        print(f"  Points: {q['points']}")
        print(f"  Stored correct answer: '{q['answer']}' (type: {type(q['answer'])})")
        print(f"  User answer: '{ans}' (type: {type(ans)})")
        
        if q['type'] in ['mcq', 'tf']:
            # Exact match (for MCQ usually option text or index, for TF "True"/"False")
            if q['answer'] == ans:
                correct = True
                score += q['points']
                print(f"  -> CORRECT! +{q['points']} points (running score: {score})")
            else:
                print(f"  -> Incorrect (no points added, running score: {score})")
        elif q['type'] == 'short':
            if ans.lower() == q['answer'].lower():
                correct = True
                score += q['points']
                print(f"  -> CORRECT! +{q['points']} points (running score: {score})")
            else:
                print(f"  -> Incorrect (no points added, running score: {score})")
        
        scored_answers.append({
            'question': q['text'],
            'answer': ans,
            'correct': correct,
            'type': q['type']
        })
    
    percentage = (score / total * 100) if total > 0 else 0
    
    # Final debug
    print(f"\n=== FINAL RESULTS ===\nFinal score: {score}/{total} ({percentage:.1f}%)\nUser: {username}\nQuiz: {quiz.get('title')}\n")
    
    # Save to DB
    results.insert_one({
    'user': ObjectId(current_user.id),
    'username': current_user.username,  # ← ADD THIS LINE (or current_user.name if that's the field)
    'quiz': quiz_oid,
    'answers': scored_answers,
    'score': score,
    'total': total,
    'percentage': percentage,
    'date': datetime.now()
}).inserted_id
    
    print(f"Result saved to DB with ID: {result_id}\n=== SUBMISSION END ===\n")
    
    return render_template('results.html', score=score, total=total, percentage=percentage,
                           answers=scored_answers, quiz_title=quiz['title'])

@app.route('/my_results')
@login_required
def my_results():
    users, quizzes, results = get_collections()

    pipeline = [
        # Lookup quiz title
        {
            '$lookup': {
                'from': 'quizzes',
                'localField': 'quiz',
                'foreignField': '_id',
                'as': 'quizInfo'
            }
        },
        {'$unwind': {'path': '$quizInfo', 'preserveNullAndEmptyArrays': True}},

        # Lookup student username
        {
            '$lookup': {
                'from': 'users',
                'localField': 'user',
                'foreignField': '_id',
                'as': 'userInfo'
            }
        },
        {'$unwind': {'path': '$userInfo', 'preserveNullAndEmptyArrays': True}},

        # Project clean fields
        {
            '$project': {
                'quizTitle': {'$ifNull': ['$quizInfo.title', 'Unknown']},
                'studentUsername': {'$ifNull': ['$userInfo.username', 'Unknown User']},
                'score': 1,
                'total': 1,
                'percentage': {'$round': [{'$ifNull': ['$percentage', 0]}, 1]},  # Rounds nicely, handles missing
                'date': 1
            }
        },

        {'$sort': {'date': -1}}
    ]

    # Add filter for students only (faster + private)
    if current_user.role == 'student':
        pipeline.insert(0, {'$match': {'user': ObjectId(current_user.id)}})

    # For masters: no $match needed (gets all), but safe if you want
    # else:
    #     pipeline.insert(0, {'$match': {}})

    try:
        res_list = list(results.aggregate(pipeline))
    except Exception as e:
        flash('Error loading results — check server logs')
        print(f"Aggregation error: {e}")
        res_list = []

    return render_template('my_results.html', results=res_list)

@app.route('/leaderboard')
@login_required
def leaderboard():
    users, quizzes, results = get_collections()
    
    pipeline = [
        {"$group": {
            "_id": "$username",                  # group by username
            "totalScore": {"$sum": "$score"}     # sum the score field
        }},
        {"$sort": {"totalScore": -1}},           # highest first
        {"$limit": 10},
        {"$project": {                           # reshape output for template
            "username": "$_id",
            "totalScore": 1,
            "_id": 0
        }}
    ]
    
    try:
        leaderboard_data = list(results.aggregate(pipeline))
        print("Leaderboard raw data:", leaderboard_data)  # ← shows in Vercel logs for debug
    except Exception as e:
        flash('Error loading leaderboard', 'danger')
        print("Leaderboard aggregation error:", str(e))
        leaderboard_data = []
    
    # Fallback: if no scores yet, show empty
    if not leaderboard_data:
        leaderboard_data = [{"username": "No scores yet", "totalScore": 0}]
    
    return render_template('leaderboard.html', leaderboard=leaderboard_data, user=current_user)

@app.route('/edit_quiz/<quiz_id>', methods=['GET', 'POST'])
@login_required
def edit_quiz(quiz_id):
    if current_user.role != 'master':
        flash('Access denied')
        return redirect(url_for('dashboard'))

    users, quizzes, results = get_collections()
    quiz_oid = safe_object_id(quiz_id)
    if not quiz_oid:
        flash('Invalid quiz ID')
        return redirect(url_for('quizzes'))

    quiz = quizzes.find_one({'_id': quiz_oid, 'createdBy': ObjectId(current_user.id)})
    if not quiz:
        flash('Quiz not found or access denied')
        return redirect(url_for('quizzes'))

    if request.method == 'POST':
        # Re-use similar validation logic as create_quiz
        title = request.form.get('title', '').strip()
        subject = request.form.get('subject', '').strip()
        duration_str = request.form.get('duration', '0')

        try:
            duration = int(duration_str)
            if duration <= 0:
                raise ValueError
        except ValueError:
            flash('Invalid duration')
            return render_template('edit_quiz.html', quiz=quiz)

        if not title or not subject:
            flash('Title and subject required')
            return render_template('edit_quiz.html', quiz=quiz)

        questions = []
        i = 1
        while True:
            q_text = request.form.get(f'q_text_{i}', '').strip()
            if not q_text:
                break
            # (same validation as create_quiz – omitted for brevity but copy-paste it here)
            # ... build q dict exactly like in create_quiz ...

        if len(questions) == 0:
            flash('At least one question required')
            return render_template('edit_quiz.html', quiz=quiz)

        quizzes.update_one(
            {'_id': quiz_oid},
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

    return render_template('edit_quiz.html', quiz=quiz)

@app.route('/delete_quiz/<quiz_id>', methods=['GET', 'POST'])
@login_required
def delete_quiz(quiz_id):
    if current_user.role != 'master':
        flash('Access denied')
        return redirect(url_for('dashboard'))

    users, quizzes, results = get_collections()
    quiz_oid = safe_object_id(quiz_id)
    if not quiz_oid:
        flash('Invalid quiz ID')
        return redirect(url_for('quizzes'))

    quiz = quizzes.find_one({'_id': quiz_oid, 'createdBy': ObjectId(current_user.id)})
    if quiz:
        quizzes.delete_one({'_id': quiz_oid})
        flash('Quiz deleted successfully!')

    return redirect(url_for('quizzes'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
