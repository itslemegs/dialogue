import sqlite3

# Connect to your SQLite database (creates app.db if it doesn't exist)
conn = sqlite3.connect("consensus.db")
cursor = conn.cursor()

# Disable foreign key constraints
cursor.execute("PRAGMA foreign_keys = OFF;")

# Drop tables if they exist
cursor.execute("DROP TABLE IF EXISTS proposal_message;")
cursor.execute("DROP TABLE IF EXISTS proposal_room;")

# Re-enable foreign key constraints
cursor.execute("PRAGMA foreign_keys = ON;")

# Commit changes and close connection
conn.commit()
conn.close()