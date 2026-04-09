"""
Terminal chat interface for Nami — run this to chat without the web UI.
Usage: .venv\Scripts\python chat_terminal.py
"""
from dotenv import load_dotenv
load_dotenv()

from app.db import SessionLocal
from app.models import User
from app.agent import process_message

def main():
    db = SessionLocal()

    # Pick the user
    users = db.query(User).all()
    if not users:
        print("No users found. Please login via the web UI first.")
        db.close()
        return

    if len(users) == 1:
        user = users[0]
    else:
        print("Select account:")
        for i, u in enumerate(users):
            print(f"  [{i+1}] {u.email}")
        choice = input("Enter number: ").strip()
        try:
            user = users[int(choice) - 1]
        except (ValueError, IndexError):
            user = users[0]

    print(f"\n{'='*50}")
    print(f"  Nami — AI Virtual Assistant")
    print(f"  Logged in as: {user.email}")
    print(f"  Type 'quit' or 'exit' to stop")
    print(f"{'='*50}\n")

    while True:
        try:
            text = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break

        if not text:
            continue
        if text.lower() in ("quit", "exit", "bye"):
            print("Nami: Goodbye! Have a great day.")
            break

        try:
            reply = process_message(user, text, db)
            print(f"\nNami: {reply}\n")
        except Exception as e:
            print(f"\n[Error]: {e}\n")

    db.close()

if __name__ == "__main__":
    main()
