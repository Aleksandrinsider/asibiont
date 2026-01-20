"""Script to create promo codes for Bronze, Silver, and Gold tiers"""
import os
import sys
from datetime import datetime, timedelta, timezone
from models import Session, PromoCode, SubscriptionTier

def create_tier_promo_codes():
    """Create 3 promo codes - one for each tier (Bronze, Silver, Gold)"""
    session = Session()
    try:
        print("Creating promo codes for all tiers...")
        
        # Set expiration date to 1 year from now
        expires_at = datetime.now(timezone.utc) + timedelta(days=365)
        
        promo_codes_data = [
            {
                'code': 'BRONZE30',
                'tier': SubscriptionTier.BRONZE,
                'duration_days': 30,
                'description': 'Bronze tier - 1 month'
            },
            {
                'code': 'SILVER30',
                'tier': SubscriptionTier.SILVER,
                'duration_days': 30,
                'description': 'Silver tier - 1 month'
            },
            {
                'code': 'GOLD30',
                'tier': SubscriptionTier.GOLD,
                'duration_days': 30,
                'description': 'Gold tier - 1 month'
            }
        ]
        
        created_codes = []
        for promo_data in promo_codes_data:
            # Check if promo code already exists
            existing = session.query(PromoCode).filter_by(code=promo_data['code']).first()
            if existing:
                print(f"⚠️  Promo code {promo_data['code']} already exists, skipping...")
                continue
            
            # Create new promo code
            promo = PromoCode(
                code=promo_data['code'],
                tier=promo_data['tier'],
                discount_percent=0,
                max_uses=None,  # Unlimited uses
                duration_days=promo_data['duration_days'],
                expires_at=expires_at,
                is_used=False,
                used_count=0,
                used_by_users='[]'
            )
            session.add(promo)
            created_codes.append(promo_data)
            print(f"✓ Created: {promo_data['code']} - {promo_data['description']}")
        
        session.commit()
        
        if created_codes:
            print(f"\n✅ Successfully created {len(created_codes)} promo code(s)!")
            print("\nPromo Codes:")
            print("=" * 50)
            for code_data in created_codes:
                print(f"Code: {code_data['code']}")
                print(f"Tier: {code_data['tier'].value}")
                print(f"Duration: {code_data['duration_days']} days")
                print(f"Max uses: Unlimited (one per user)")
                print(f"Expires: {expires_at.strftime('%Y-%m-%d')}")
                print("-" * 50)
        else:
            print("\n⚠️  All promo codes already exist in database")
            
    except Exception as e:
        session.rollback()
        print(f"\n❌ Error creating promo codes: {e}")
        raise
    finally:
        session.close()

if __name__ == "__main__":
    print("=" * 50)
    print("CREATING PROMO CODES FOR ALL TIERS")
    print("=" * 50)
    print()
    
    create_tier_promo_codes()
    
    print()
    print("=" * 50)
    print("DONE!")
    print("=" * 50)
