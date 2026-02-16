import asyncio
from spond import Spond
import os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
import time
import random



load_dotenv()

async def check_volleyball_events():
    s = Spond(os.getenv("Spond_USERNAME"), os.getenv("Spond_PASSWORD"), os.getenv("Spond_ID"))
    start_time = datetime.now(timezone.utc)
    threshold = timedelta(minutes=5)
    events = None
    while events is None:
        try:
            events = await s.get_upcoming_events(includeDeclined=True, minEndTimestamp=start_time)
        except Exception as e:
            print(f"An error occurred: {e}")
            cooldown = random.randint(1, 5)
            print(f"Cooling down for {cooldown} seconds.")
            await asyncio.sleep(cooldown)

    nextEventID = events[0]['id']

    while True:
        if datetime.now(timezone.utc) > start_time + threshold:
            print("Threshold reached. Exiting.")
            break
        try:
            response = await s.give_answer(nextEventID, True)
            print(response)
            break

        except Exception as e:
            error_msg = str(e)
            print(f"Attempt failed: {error_msg}")
            if "403" in error_msg:
                cooldown = random.randint(1, 5)
                print(f"Rate limit or forbidden. Cooling down for {cooldown} seconds.")
                await asyncio.sleep(cooldown)
            else:
                cooldown = random.randint(20, 40)
                print(f"Unknown error. Cooling down for {cooldown} seconds.")
                await asyncio.sleep(cooldown)

    await s.clientsession.close()


async def main():
    await check_volleyball_events()

if __name__ == "__main__":
    asyncio.run(main())