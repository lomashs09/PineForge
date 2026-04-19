"""Show transactions for a given user email."""
import asyncio
import sys
from sqlalchemy import text
from api.database import async_session

EMAIL = sys.argv[1] if len(sys.argv) > 1 else "lomashs09@gmail.com"

async def show():
    async with async_session() as db:
        r = await db.execute(
            text(
                "SELECT t.type, t.amount, t.balance_after, t.description, "
                "t.reference_id, t.created_at "
                "FROM transactions t JOIN users u ON t.user_id = u.id "
                "WHERE u.email = :email ORDER BY t.created_at DESC"
            ),
            {"email": EMAIL},
        )
        rows = r.all()
        if not rows:
            print("No transactions found for", EMAIL)
            return
        print("Found %d transaction(s) for %s:\n" % (len(rows), EMAIL))
        for i, row in enumerate(rows, 1):
            print("--- #%d ---" % i)
            print("  Type:          %s" % row[0])
            print("  Amount:        $%.4f" % row[1])
            print("  Balance After: $%.4f" % row[2])
            print("  Description:   %s" % row[3])
            print("  Reference:     %s" % row[4])
            print("  Time:          %s" % row[5])
            print()

asyncio.run(show())
