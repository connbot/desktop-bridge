"""Analyze explicitly fictional sample spending with decimal-safe arithmetic."""
import csv
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with (root / 'data' / 'sample-spending.csv').open(newline='') as source:
    rows = list(csv.DictReader(source))
categories = defaultdict(Decimal)
total = Decimal('0')
for row in rows:
    amount = Decimal(row['amount'])
    categories[row['category']] += amount
    total += amount
    row['amount'] = float(amount)
summary = {
    'data_classification': 'fictional sample data, not personal financial records',
    'total': float(total),
    'categories': {category: float(amount) for category, amount in categories.items()},
    'transactions': rows,
}
assert total == Decimal('126.40'), total
(root / 'outputs').mkdir(exist_ok=True)
(root / 'outputs' / 'spending-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
(root / 'outputs' / 'spending-summary.md').write_text(
    '# Sample spending snapshot\n\nFictional data only.\n\n'
    + f'Total: ${total:.2f} across {len(rows)} sample transactions.\n\n'
    + '\n'.join(f'- {category}: ${amount:.2f}' for category, amount in categories.items())
    + '\n'
)
print(json.dumps({'total_usd':str(total),'transactions':len(rows),'files_written':2}))
