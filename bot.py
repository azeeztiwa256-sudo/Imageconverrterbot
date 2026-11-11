imageconverterbot/
├── bot.py                  # entry point
├── config.py               # env loading
├── db.py                   # SQLite (async wrapper)
├── handlers/
│   ├── __init__.py
│   ├── start.py            # /start, /help, /about
│   ├── convert.py          # core conversion flow
│   ├── tools.py            # compress / resize / grayscale / rotate / watermark
│   ├── batch.py            # batch + PDF
│   └── admin.py            # /admin /stats /broadcast /maintenance /logs
├── services/
│   ├── __init__.py
│   ├── imaging.py          # all Pillow work, run in thread pool
│   ├── files.py            # safe temp file handling, validation
│   └── stats.py            # counters
├── keyboards.py            # inline keyboards
├── utils.py                # logging, misc
├── requirements.txt
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── tests/
│   └── test_imaging.py
└── README.md
