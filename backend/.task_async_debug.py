import asyncio, faulthandler
faulthandler.dump_traceback_later(3, repeat=False)
print('before')
async def main(): print('inside')
asyncio.run(main())
print('after')
