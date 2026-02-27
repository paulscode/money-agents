# Money Agents Backend

FastAPI-based backend for the Money Agents system.

## Development

**All development happens in Docker containers.** Use the main `dev.sh` script from the project root:

```bash
cd ..
bash dev.sh start    # Start all services in Docker
bash dev.sh logs backend  # View backend logs
bash dev.sh exec backend bash  # Access backend shell
```

## Local Testing / Scripts

If you need to run Python scripts or tests locally (not in Docker):

### 1. Create Python Virtual Environment

```bash
cd backend
python3.12 -m venv venv
source venv/bin/activate  # On Linux/Mac
# or
venv\Scripts\activate  # On Windows
```

### 2. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure Environment

Make sure your `.env` file in the project root has the required variables:

```env
# Application
SECRET_KEY=your-secret-key-here
DEBUG=True

# Database
DATABASE_URL=postgresql+asyncpg://username:password@localhost:5432/money_agents

# Redis
REDIS_URL=redis://localhost:6379/0

# AI Services
OPENAI_API_KEY=your-openai-key
ANTHROPIC_API_KEY=your-anthropic-key
ELEVENLABS_API_KEY=your-elevenlabs-key
```

### 4. Run Database Migrations

```bash
# In Docker (preferred)
bash dev.sh exec backend alembic upgrade head

# Or locally (if venv activated and services running)
alembic upgrade head
```

This starts PostgreSQL and Redis containers.

### 5. Run Database Migrations

```bash
# Create initial migration
alembic revision --autogenerate -m "Initial migration"

# Apply migrations
alembic upgrade head
```

### 6. Run the Development Server

```bash
# From the backend directory
python -m app.main

# Or use uvicorn directly
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at:
- API: http://localhost:8000
- Interactive API docs: http://localhost:8000/docs
- Alternative docs: http://localhost:8000/redoc

## Project Structure

```
backend/
├── alembic/              # Database migrations
│   ├── versions/         # Migration scripts
│   └── env.py           # Alembic configuration
├── app/
│   ├── api/             # API layer
│   │   ├── endpoints/   # API route handlers
│   │   │   ├── auth.py
│   │   │   ├── users.py
│   │   │   ├── proposals.py
│   │   │   ├── campaigns.py
│   │   │   └── conversations.py
│   │   ├── deps/        # Dependencies (auth, etc.)
│   │   └── api.py       # API router
│   ├── core/            # Core functionality
│   │   ├── config.py    # Settings management
│   │   ├── database.py  # Database connection
│   │   └── security.py  # Auth & security
│   ├── models/          # SQLAlchemy models
│   ├── schemas/         # Pydantic schemas
│   ├── services/        # Business logic
│   ├── agents/          # Agent implementations
│   └── main.py          # Application entry point
├── tests/               # Test suite
├── alembic.ini          # Alembic config file
└── requirements.txt     # Python dependencies
```

## API Endpoints

### Authentication
- `POST /api/v1/auth/register` - Register new user
- `POST /api/v1/auth/login` - Login and get token

### Users
- `GET /api/v1/users/me` - Get current user
- `PUT /api/v1/users/me` - Update current user
- `GET /api/v1/users/{user_id}` - Get user by ID

### Proposals
- `POST /api/v1/proposals/` - Create proposal
- `GET /api/v1/proposals/` - List proposals
- `GET /api/v1/proposals/{id}` - Get proposal
- `PUT /api/v1/proposals/{id}` - Update proposal
- `DELETE /api/v1/proposals/{id}` - Delete proposal

### Campaigns
- `POST /api/v1/campaigns/` - Create campaign
- `GET /api/v1/campaigns/` - List campaigns
- `GET /api/v1/campaigns/{id}` - Get campaign
- `PUT /api/v1/campaigns/{id}` - Update campaign
- `DELETE /api/v1/campaigns/{id}` - Delete campaign

### Conversations
- `POST /api/v1/conversations/` - Create conversation
- `GET /api/v1/conversations/` - List conversations
- `GET /api/v1/conversations/{id}` - Get conversation
- `POST /api/v1/conversations/{id}/messages` - Send message
- `GET /api/v1/conversations/{id}/messages` - Get messages

## Development

### Running Tests

```bash
pytest
pytest --cov=app tests/  # With coverage
```

### Code Formatting

```bash
black app/
ruff check app/ --fix
```

### Type Checking

```bash
mypy app/
```

### Database Commands

```bash
# Create a new migration
alembic revision --autogenerate -m "Description"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# Show current revision
alembic current

# Show migration history
alembic history
```

## Next Steps

1. ✅ Backend structure created
2. ✅ Database models defined
3. ✅ Basic API endpoints implemented
4. ⏳ Add agent implementations
5. ⏳ Add services layer
6. ⏳ Add comprehensive tests
7. ⏳ Add WebSocket support
8. ⏳ Integrate AI services
