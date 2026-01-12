import requests
TOKEN='8310820990:AAFb-Mw5rnntbYdYom0St7K3gIwObEUpD9k'
base=f'https://api.telegram.org/bot{TOKEN}'
try:
    r = requests.get(base + '/getMe', timeout=10)
    print('GETME_OK', r.status_code, r.json())
except Exception as e:
    print('GETME_ERR', e)
try:
    r2 = requests.get(base + '/getWebhookInfo', timeout=10)
    print('WEBHOOK', r2.status_code, r2.json())
except Exception as e:
    print('WEBHOOK_ERR', e)
