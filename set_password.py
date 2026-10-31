import bcrypt
import sqlite3

# 生成已知密码的 bcrypt 哈希
password = "Admin123!"
hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
print(f"New hash for '{password}': {hashed}")

conn = sqlite3.connect('E:/erp系统/backend/erp.db')
cursor = conn.cursor()
cursor.execute("UPDATE sys_user SET password_hash = ? WHERE username = 'admin'", (hashed,))
conn.commit()
print(f"Updated admin password. Rows affected: {cursor.rowcount}")
conn.close()
