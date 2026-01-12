import requests
TOKEN='8310820990:AAFb-Mw5rnntbYdYom0St7K3gIwObEUpD9k'
try:
    r = requests.post(f'https://api.telegram.org/bot{TOKEN}/setWebhook', data={'url':'https://task-production-1d10.up.railway.app/webhook'}, timeout=10)
    print('status', r.status_code, r.text)
except Exception as e:
    print('err', e)
