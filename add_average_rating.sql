-- Add average_rating column to users table
-- Run this in Railway database console

ALTER TABLE users ADD COLUMN IF NOT EXISTS average_rating INTEGER DEFAULT 0;

-- Verify the column was added
SELECT column_name FROM information_schema.columns
WHERE table_name = 'users' AND column_name = 'average_rating';