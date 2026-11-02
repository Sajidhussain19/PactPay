# PactPay

AI-verified escrow for freelance work. The client's payment is held by PayPal, an AI checks the
delivered work against the contract with evidence, and the money is released when it passes.

> Work in progress for the PayPal AI Hackathon. See [docs/architecture.md](docs/architecture.md).

## Quickstart (backend)

```bash
cd backend
python -m venv .venv && . .venv/Scripts/activate   # macOS/Linux: . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload    # http://localhost:8000/health
```

## License

MIT
