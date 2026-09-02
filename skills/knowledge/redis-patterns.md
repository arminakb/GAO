---
description: Redis patterns — caching strategies, distributed locks, rate limiting, pub/sub vs streams, key design, connection pooling, and eviction policies.
tags: redis, caching, rate-limiting, distributed-systems
origin: ECC
---

# Redis Patterns

Redis best practices across common backend use cases: cache-aside loading,
atomic rate limiting, safe distributed locks, and durable event streams.
Individual commands are atomic on a single instance; multi-step workflows
need Lua scripts, `MULTI`/`EXEC`, or explicit synchronization to stay atomic.

## When to Reference

- Adding caching to an application
- Implementing rate limiting or throttling
- Building distributed locks or coordination
- Using Pub/Sub or Redis Streams for messaging
- Configuring Redis in production (pooling, eviction, clustering)

## Data Structure Cheat Sheet

| Use case | Structure | Example key |
|----------|-----------|-------------|
| Simple cache | String | `product:123` |
| User session | Hash | `session:abc` |
| Leaderboard | Sorted Set | `scores:weekly` |
| Unique visitors | Set | `visitors:2024-01-01` |
| Event stream | Stream | `events:orders` |
| Counters / rate limits | String (INCR) | `ratelimit:user:123` |

## Core Patterns

### Cache-Aside (Lazy Loading)

```python
def get_product(product_id: int) -> dict:
    cache_key = f"product:{product_id}"
    cached = r.get(cache_key)
    if cached:
        return json.loads(cached)
    product = db.query("SELECT * FROM products WHERE id = %s", product_id)
    r.setex(cache_key, 3600, json.dumps(product))  # TTL: 1 hour
    return product
```

Write-through (DB write, then immediate `setex`) when consistency outranks
read latency. Tag-based invalidation groups related keys under a set so a
category expiry deletes them in one step.

### Rate Limiting

Fixed window (simple, low traffic):

```python
def is_rate_limited(user_id: int, limit: int = 100, window: int = 60) -> bool:
    key = f"ratelimit:{user_id}:{int(time.time()) // window}"
    pipe = r.pipeline(transaction=True)
    pipe.incr(key)
    pipe.expire(key, window)
    count, _ = pipe.execute()
    return count > limit
```

Sliding window (accurate per-user throttling): Lua script that
`ZREMRANGEBYSCORE`s old entries, `ZCARD`s the count, and `ZADD`s a unique
`now-seq` member — atomic, so no pipeline race. Use the fixed window for
low-stakes endpoints and the sliding window when throttling accuracy matters.

### Distributed Locks (Single Node — SET NX PX)

```python
def acquire_lock(resource: str, ttl_ms: int = 5000) -> str | None:
    token = str(uuid.uuid4())
    acquired = r.set(f"lock:{resource}", token, px=ttl_ms, nx=True)
    return token if acquired else None

def release_lock(resource: str, token: str) -> bool:
    # Lua: only delete if the stored token matches — never release
    # someone else's lock after your TTL expired
    release_script = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    else
        return 0
    end
    """
    return bool(r.eval(release_script, 1, f"lock:{resource}", token))
```

Rules: TTL must exceed the expected work duration; always release in
`finally`; unique tokens prevent releasing a lock your process no longer
owns. Multi-node setups use `redlock-py`.

### Pub/Sub vs Streams

- **Pub/Sub**: fire-and-forget broadcast; no delivery guarantees; late
  subscribers miss everything.
- **Streams**: durable queue with consumer groups (`XADD` with `maxlen`,
  `XREADGROUP`, `XACK`) — at-least-once delivery and replay. Prefer Streams
  whenever delivery guarantees, consumer groups, or replay matter.

## Key Design

Naming: `resource:id:field` (`user:123:profile`) or namespaced
`myapp:ratelimit:user:123`; time-bound keys carry the date
(`stats:pageviews:2024-01-01`).

TTL rules of thumb: sessions 24h; API response cache 5–15 min; rate-limit
windows match the window; short-lived tokens 5–10 min. **Always set a TTL** —
keys without one accumulate until memory pressure forces evictions.

## Connection Management

```python
pool = ConnectionPool(host="localhost", port=6379, max_connections=20,
                      decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
r = Redis(connection_pool=pool)
```

Size the pool to the workload (exhaustion under load is a classic outage).
Cluster mode via `RedisCluster(startup_nodes=[...])`; HA via Sentinel
(`master_for` / `slave_for`).

## Eviction Policies

| Policy | Behavior | Best for |
|--------|----------|----------|
| `noeviction` | Error on write when full | Queues / critical data |
| `allkeys-lru` | Evict least recently used | General cache |
| `volatile-lru` | LRU among keys with TTL | Mixed data store |
| `allkeys-lfu` | Evict least frequently used | Skewed access patterns |

## Anti-Patterns

| Anti-pattern | Problem | Fix |
|---|---|---|
| Keys with no TTL | Unbounded memory growth | Always set TTL |
| `KEYS *` in production | Blocks the server (O(N)) | Use `SCAN` cursor |
| Large blobs (>100KB) | Slow serialization, memory pressure | Store reference, fetch from object store |
| One Redis for everything | No isolation between cache & queue | Separate instances or DBs |
| Ignoring pool limits | Connection exhaustion under load | Size pool to workload |
| Unhandled cache-miss stampede | Thundering herd on cold start | Lock or probabilistic early expiry |

Cache-miss stampede prevention: per-key in-process `threading.Lock` with a
double-check after acquiring (re-`get` before fetching); for multi-process
deployments, replace with the Redis distributed lock above.

## Quick Reference

| Pattern | When to use |
|---------|-------------|
| Cache-aside | Read-heavy, tolerates slight staleness |
| Write-through | Strong consistency required |
| Distributed lock | Prevent concurrent access to a resource |
| Sliding window rate limit | Accurate per-user throttling |
| Redis Streams | Durable event queue with consumer groups |
| Pub/Sub | Broadcast, no delivery guarantees needed |
| Sorted Set leaderboard | Ranked scoring, pagination |
| HyperLogLog | Approximate unique count at low memory |
