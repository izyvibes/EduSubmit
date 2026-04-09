import sqlite3

conn = sqlite3.connect('database.db')
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    fullname TEXT,
    matric TEXT,
    course TEXT,
    filename TEXT,
    text_answer TEXT
)
""")

conn.commit()
conn.close()

print("Submissions table created!")