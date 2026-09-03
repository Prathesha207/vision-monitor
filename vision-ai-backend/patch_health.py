with open('app/main.py', 'r', encoding='utf-8') as f:
    code = f.read()

code = code.replace('"status": True', '"status": "ready"')

with open('app/main.py', 'w', encoding='utf-8') as f:
    f.write(code)
print('Fixed health endpoint to return ready')

