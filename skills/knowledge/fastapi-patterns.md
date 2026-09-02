---
description: FastAPI best practices — project structure, Pydantic v2 schemas, dependency injection, async handlers, transactional service layers, and testing with httpx/pytest.
tags: fastapi, python, api, pydantic, testing
origin: ECC
---

# FastAPI Patterns

Production-grade FastAPI development: app factory, Pydantic v2 schemas,
dependency injection, transactional service layer, deterministic pagination,
and integration testing. Pairs with `error-handling` for the stable error
envelope and `database-patterns` for schema/migration rules.

## Project Structure

```text
my_app/
|-- app/
|   |-- main.py               # App factory, lifespan, middleware
|   |-- config.py             # Settings via pydantic-settings
|   |-- dependencies.py       # Shared FastAPI dependencies
|   |-- routers/              # Thin HTTP adapters
|   |-- models/               # ORM models (persistence layer)
|   |-- schemas/              # Pydantic request/response schemas
|   `-- services/             # Business logic layer
`-- tests/
```

Routes stay thin; business logic lives in services; ORM models never leak
into schemas (explicit `response_model` on every route prevents accidental
PII leaks and keeps OpenAPI clean).

## App Factory and Lifespan

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev/demo: create tables on startup. Production: Alembic migrations.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()

def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    app.include_router(users.router, prefix="/users", tags=["users"])
    return app
```

Use an app factory (not module-level `app = FastAPI()`) so tests can build
isolated instances with overridden dependencies.

## Configuration with pydantic-settings

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    app_name: str = "My App"
    database_url: str
    secret_key: str
    allowed_origins: list[str] = ["http://localhost:3000"]
```

Never read `os.environ` ad hoc in handlers; everything flows through the
typed `Settings` object.

## Dependency Injection

```python
from typing import Annotated
from fastapi import Depends

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

DbDep = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]
```

- Type-alias shared dependencies (`DbDep`) — one line per handler instead of
  a nested `Depends` chain.
- Separate authentication (`get_current_user`, 401) from authorization
  (`get_current_active_user`, 403) so REST status signals stay precise.

## Router and Service Layer

```python
@router.post("/", response_model=UserResponse, status_code=201)
async def create_user(payload: UserCreate, db: DbDep) -> UserResponse:
    try:
        return await UserService(db).create(payload)
    except DuplicateUserError:
        raise HTTPException(status_code=400, detail="Email already registered")
```

```python
class UserService:
    async def create(self, payload: UserCreate) -> User:
        user = User(email=payload.email, hashed_password=pwd_context.hash(payload.password))
        self.db.add(user)
        try:
            # Rely on atomic DB constraints, not race-prone app-level prechecks
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise DuplicateUserError from exc
        await self.db.refresh(user)
        return user
```

Application-level uniqueness handling requires an underlying unique database
constraint — without it, error-catching cannot prevent concurrent races.

## Pagination and Query Hygiene

```python
@router.get("/", response_model=UserListResponse)
async def list_users(
    db: DbDep,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> UserListResponse:
    ...
```

- **Always** bound `limit` (`le=100`) and enforce deterministic ordering
  (`.order_by(Model.id)`) on offset/limit pagination — unsorted pages skip
  and duplicate rows.
- Count queries for totals; never return unbounded collections.

## Error Handling Integration

Domain services raise typed errors (`error-handling`); the transport layer
converts them. Register handlers once at app creation:

```python
@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code,
                        content={"error": {"code": exc.code, "message": str(exc)}})

@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Keep framework validation inside the project's stable envelope —
    # a bare 422 with FastAPI's default `detail` shape is a contract breach.
    return JSONResponse(status_code=422,
                        content={"error": {"code": "VALIDATION_ERROR", "message": "Request validation failed"}})
```

## Testing with httpx and pytest

```python
from httpx import ASGITransport, AsyncClient

@pytest_asyncio.fixture
async def client(db_session):
    app = create_app()
    async def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
```

Override `get_db` with a per-test session; exercise endpoints through HTTP
(`AsyncClient` + `ASGITransport`), not by calling handlers directly — that
validates serialization, status codes, and the error envelope too.

## Anti-Patterns

```python
# BAD: business logic inside route handlers (untestable, duplicated)
@router.post("/users/")
async def create_user(payload: UserCreate, db: DbDep):
    db.add(User(email=payload.email, hashed_password=bcrypt.hash(payload.password)))
    await db.commit()

# BAD: sync DB calls in async routes — blocks the event loop
@router.get("/items/")
async def list_items(db: Session = Depends(get_db)):
    return db.query(Item).all()
```

## Checklist

- [ ] Typed `response_model` on every route
- [ ] Thin routes; all logic in transactional services
- [ ] Async session end-to-end (no sync calls in async handlers)
- [ ] `RequestValidationError` overridden to the stable error envelope
- [ ] Deterministic ordering + bounded `limit` on all list endpoints
- [ ] App factory pattern; dependencies overridden in tests
