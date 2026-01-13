import requests
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN='8310820990:AAFb-Mw5rnntbYdYom0St7K3gIwObEUpD9k'
try:
    r = requests.post(f'https://api.telegram.org/bot{TOKEN}/setWebhook', data={'url':'https://task-production-1d10.up.railway.app/webhook'}, timeout=10)
    logger.info(f'Webhook status: {r.status_code}, response: {r.text}')
except Exception as e:
    logger.error(f'Webhook error: {e}')
