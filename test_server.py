import os
import asyncio
from dotenv import load_dotenv

# Force local mode for testing
os.environ['LOCAL'] = '1'
os.environ['FREE_ACCESS_MODE'] = 'true'

load_dotenv()

# Now import and run
import main

if __name__ == "__main__":
    print("Starting server in LOCAL mode...")
    asyncio.run(main.run_server())