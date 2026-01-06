"""
Add rating API endpoint to main.py
This code should be added to main.py
"""

# Add this endpoint after other API handlers

async def rate_user_handler(request):
    """Rate another user (1-10 scale)"""
    try:
        session_req = await get_session(request)
        user_id = session_req.get('user_id')
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)
        
        data = await request.json()
        rated_username = data.get('username')
        rating = data.get('rating')
        
        if not rated_username or not rating:
            return web.json_response({'error': 'Missing username or rating'}, status=400)
        
        if not (1 <= rating <= 10):
            return web.json_response({'error': 'Rating must be between 1 and 10'}, status=400)
        
        session_db = Session()
        try:
            # Get rater user
            rater = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not rater:
                return web.json_response({'error': 'User not found'}, status=404)
            
            # Get rated user
            rated_user = session_db.query(User).filter(User.username.ilike(rated_username.replace('@', ''))).first()
            if not rated_user:
                return web.json_response({'error': 'Rated user not found'}, status=404)
            
            # Can't rate yourself
            if rater.id == rated_user.id:
                return web.json_response({'error': 'Cannot rate yourself'}, status=400)
            
            # Check if rating already exists
            from models import UserRating
            existing_rating = session_db.query(UserRating).filter_by(
                rater_user_id=rater.id,
                rated_user_id=rated_user.id
            ).first()
            
            if existing_rating:
                # Update existing rating
                existing_rating.rating = rating
                existing_rating.updated_at = datetime.datetime.now(datetime.timezone.utc)
            else:
                # Create new rating
                new_rating = UserRating(
                    rater_user_id=rater.id,
                    rated_user_id=rated_user.id,
                    rating=rating
                )
                session_db.add(new_rating)
            
            session_db.commit()
            
            # Recalculate average rating for rated user
            all_ratings = session_db.query(UserRating).filter_by(rated_user_id=rated_user.id).all()
            if all_ratings:
                avg_rating = sum(r.rating for r in all_ratings) / len(all_ratings)
                rated_profile = session_db.query(UserProfile).filter_by(user_id=rated_user.id).first()
                if rated_profile:
                    rated_profile.average_rating = round(avg_rating, 1)
                    rated_profile.rating_count = len(all_ratings)
                    session_db.commit()
            
            return web.json_response({
                'success': True,
                'message': f'Оценка {rating}/10 для @{rated_username} сохранена'
            })
        
        finally:
            session_db.close()
    
    except Exception as e:
        logger.error(f"Error rating user: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def get_user_rating_handler(request):
    """Get current user's rating for another user"""
    try:
        session_req = await get_session(request)
        user_id = session_req.get('user_id')
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)
        
        rated_username = request.rel_url.query.get('username')
        if not rated_username:
            return web.json_response({'error': 'Missing username'}, status=400)
        
        session_db = Session()
        try:
            rater = session_db.query(User).filter_by(telegram_id=user_id).first()
            rated_user = session_db.query(User).filter(User.username.ilike(rated_username.replace('@', ''))).first()
            
            if not rater or not rated_user:
                return web.json_response({'rating': None})
            
            from models import UserRating
            existing_rating = session_db.query(UserRating).filter_by(
                rater_user_id=rater.id,
                rated_user_id=rated_user.id
            ).first()
            
            if existing_rating:
                return web.json_response({'rating': existing_rating.rating})
            else:
                return web.json_response({'rating': None})
        
        finally:
            session_db.close()
    
    except Exception as e:
        logger.error(f"Error getting rating: {e}")
        return web.json_response({'error': str(e)}, status=500)


# Add these routes:
# app.router.add_post('/api/rate_user', rate_user_handler)
# app.router.add_get('/api/get_user_rating', get_user_rating_handler)
