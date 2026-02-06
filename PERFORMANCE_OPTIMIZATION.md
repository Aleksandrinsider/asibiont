# Performance Optimization Plan

## Critical Issues
- Current: 6 req/min sequential
- Required: 1000 req/min for 1000 concurrent users
- Gap: 994 req/min (163x workers needed)

## Bottlenecks Identified

### 1. Multiple AI Calls Per Request (9.78s average)
- plan_strategy(): ~3-4s (AI call)
- execute_actions(): ~4-6s (includes time_parser AI call)
- reflect_and_respond(): ~3-4s (AI call)

**Total: 3 AI calls = ~10-14s**

### 2. Time Parser (12.75s for add_task)
- Every add_task calls DeepSeek API to parse time
- No caching of common phrases

### 3. Database Queries
- No connection pooling
- User/tasks loaded on every request
- No caching

### 4. No Rate Limiting
- System vulnerable to overload

## Optimization Strategy

### Phase 1: Quick Wins (30% faster)
1. **Cache time parser results**
   - "завтра в 10:00" → cache for 1 hour
   - "через час" → cache pattern
   - **Impact**: -2s per add_task

2. **Cache user profiles**
   - TTL: 5 minutes
   - Invalidate on update
   - **Impact**: -0.5s per request

3. **Optimize AI prompts**
   - Reduce token count in planning
   - **Impact**: -1s per request

### Phase 2: Architecture (50% faster)
1. **Combine planning + response**
   - Single AI call instead of 2
   - **Impact**: -3-4s per request

2. **Background task execution**
   - Don't wait for tool execution completion
   - **Impact**: -2s for async tasks

3. **Connection pooling**
   - SQLAlchemy async engine
   - **Impact**: -0.5s per request

### Phase 3: Scaling (100x capacity)
1. **Multiple workers**
   - Gunicorn/Uvicorn workers
   - **Impact**: Linear scaling

2. **Redis caching**
   - Session cache
   - User profiles
   - Recent tasks

3. **Rate limiting**
   - Per-user: 10 req/min
   - Global: 1000 req/min

## Target Performance
- Simple chat: 2-3s (from 6.4s)
- With commands: 4-6s (from 8-13s)
- Sequential capacity: 15 req/min (from 6)
- With 20 workers: 300 req/min
- With 100 workers: 1500 req/min ✅

## Implementation Priority
1. ✅ Time parser cache (highest impact)
2. ✅ User profile cache
3. ✅ Optimize planning prompt
4. Combine planning + response
5. Connection pooling
6. Rate limiting
