with open('app/main.py', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace('allow_origins=["*"]', 'allow_origins=["*"]') # Revert the *
# actually I will just add the correct origins array
new_cors = """
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1",
    "http://localhost",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "app://localhost"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
"""
code = code.replace('allow_origins=["*"]', 'allow_origins=origins')

if "http://localhost:5173" not in code:
    code = code.replace('origins = [\n    "http://localhost:3000",\n    "http://127.0.0.1:3000",\n    "http://127.0.0.1",\n    "http://localhost",\n]', new_cors.strip())

with open('app/main.py', 'w', encoding='utf-8') as f:
    f.write(code)
print('Fixed CORS safely!')

