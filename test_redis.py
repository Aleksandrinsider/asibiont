from redis import Redis
url = 'redis://default:LnTts6f2dnlRVOf1tvwahzXZcun60kO8@redis-18169.c300.eu-central-1-1.ec2.redns.redis-cloud.com:18169'
try:
    r = Redis.from_url(url)
    ok = r.ping()
    info = r.info(section='server')
    print('REDIS_PING:', ok)
    print('REDIS_VERSION:', info.get('redis_version'))
    r.set('asb_check', 'ok', ex=10)
    print('GET asb_check:', r.get('asb_check'))
except Exception as e:
    print('REDIS_ERROR:', e)
