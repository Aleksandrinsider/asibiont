# Production Deployment Checklist

## Railway Environment Variables (Required)

### Core Services
- [ ] `DATABASE_URL` - PostgreSQL connection URL from Railway
- [ ] `REDIS_URL` - Redis connection URL from Railway
- [ ] `TELEGRAM_TOKEN` - Bot token from @BotFather
- [ ] `TELEGRAM_BOT_USERNAME` - Bot username (e.g., @yourbot)
- [ ] `DEEPSEEK_API_KEY` - AI API key from DeepSeek
- [ ] `WEBHOOK_URL` - Your Railway app URL + /webhook (e.g., https://yourapp.railway.app/webhook)
- [ ] `ENCRYPTION_KEY` - Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- [ ] `SESSION_SECRET` - Random string for session security

### Optional (can be set later)
- [ ] `WEB_APP_URL` - Your Railway app URL (defaults to Railway URL)
- [ ] `YOOKASSA_SHOP_ID` - For payment processing
- [ ] `YOOKASSA_SECRET_KEY` - For payment processing
- [ ] `YOOKASSA_WEBHOOK_URL` - For payment webhooks
- [ ] `FREE_ACCESS_MODE` - Set to "true" to skip subscription checks
- [ ] `CURRENT_DATE` - Override current date for testing

## Telegram Bot Setup
1. Create bot with @BotFather
2. Get bot token and username
3. Set domain in @BotFather: `/setdomain yourdomain.railway.app`
4. Enable inline mode (optional): `/setinline`

## Railway Deployment
1. Connect GitHub repository
2. Add PostgreSQL plugin
3. Add Redis plugin
4. Set environment variables from the list above
5. Deploy triggers automatically on git push

## Post-Deployment
- [ ] Test bot with /start command
- [ ] Check web interface at your Railway URL
- [ ] Test Telegram Login Widget
- [ ] Verify database connections
- [ ] Check logs for errors

## Monitoring
- View logs: Railway dashboard or `railway logs`
- Check Redis: Connection status in logs
- Monitor PostgreSQL: Railway metrics
- Bot uptime: Send test messages

## Security Notes
- Never commit `.env` files
- Rotate ENCRYPTION_KEY if compromised
- Use strong SESSION_SECRET (32+ chars)
- Keep TELEGRAM_TOKEN private
- Review Railway access logs regularly
