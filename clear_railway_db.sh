#!/bin/bash
# Script to clear Railway database using Railway CLI

echo "Setting CLEAR_DB=1 environment variable..."
railway variables set CLEAR_DB=1

echo "Restarting Railway application..."
railway up

echo "Waiting for restart..."
sleep 30

echo "Removing CLEAR_DB variable..."
railway variables delete CLEAR_DB

echo "Database cleared! Application restarted."