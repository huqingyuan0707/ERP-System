import sqlite3
conn = sqlite3.connect('E:/erp系统/backend/erp.db')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print('Tables:', [t[0] for t in tables])
try:
    cursor.execute('SELECT * FROM sys_user LIMIT 5')
    users = cursor.fetchall()
    print('Users:', users)
except Exception as e:
    print('User table error:', e)
try:
    cursor.execute('SELECT * FROM sys_role LIMIT 5')
    roles = cursor.fetchall()
    print('Roles:', roles)
except Exception as e:
    print('Role table error:', e)
try:
    cursor.execute('SELECT * FROM sys_permission LIMIT 10')
    perms = cursor.fetchall()
    print('Permissions:', perms)
except Exception as e:
    print('Permission table error:', e)
conn.close()
