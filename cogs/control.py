import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import hashlib
import time
import sqlite3
import asyncio
from datetime import datetime
from colorama import Fore, Style
import os
from aiohttp_socks import ProxyConnector
import random
from collections import deque

SECRET = 'tB87#kPtkxqOS2'


level_mapping = {
    31: "30-1", 32: "30-2", 33: "30-3", 34: "30-4",
    35: "FC 1", 36: "FC 1 - 1", 37: "FC 1 - 2", 38: "FC 1 - 3", 39: "FC 1 - 4",
    40: "FC 2", 41: "FC 2 - 1", 42: "FC 2 - 2", 43: "FC 2 - 3", 44: "FC 2 - 4",
    45: "FC 3", 46: "FC 3 - 1", 47: "FC 3 - 2", 48: "FC 3 - 3", 49: "FC 3 - 4",
    50: "FC 4", 51: "FC 4 - 1", 52: "FC 4 - 2", 53: "FC 4 - 3", 54: "FC 4 - 4",
    55: "FC 5", 56: "FC 5 - 1", 57: "FC 5 - 2", 58: "FC 5 - 3", 59: "FC 5 - 4",
    60: "FC 6", 61: "FC 6 - 1", 62: "FC 6 - 2", 63: "FC 6 - 3", 64: "FC 6 - 4",
    65: "FC 7", 66: "FC 7 - 1", 67: "FC 7 - 2", 68: "FC 7 - 3", 69: "FC 7 - 4",
    70: "FC 8", 71: "FC 8 - 1", 72: "FC 8 - 2", 73: "FC 8 - 3", 74: "FC 8 - 4",
    75: "FC 9", 76: "FC 9 - 1", 77: "FC 9 - 2", 78: "FC 9 - 3", 79: "FC 9 - 4",
    80: "FC 10", 81: "FC 10 - 1", 82: "FC 10 - 2", 83: "FC 10 - 3", 84: "FC 10 - 4"
}

class Control(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.conn_alliance = sqlite3.connect('db/alliance.sqlite')
        self.conn_users = sqlite3.connect('db/users.sqlite')
        self.conn_changes = sqlite3.connect('db/changes.sqlite')
        self.cursor_alliance = self.conn_alliance.cursor()
        self.cursor_users = self.conn_users.cursor()
        self.cursor_changes = self.conn_changes.cursor()
        
        self.conn_settings = sqlite3.connect('db/settings.sqlite')
        self.cursor_settings = self.conn_settings.cursor()
        self.cursor_settings.execute("""
            CREATE TABLE IF NOT EXISTS auto (
                id INTEGER PRIMARY KEY,
                value INTEGER DEFAULT 1
            )
        """)
        
        self.cursor_settings.execute("SELECT COUNT(*) FROM auto")
        if self.cursor_settings.fetchone()[0] == 0:
            self.cursor_settings.execute("INSERT INTO auto (value) VALUES (1)")
        self.conn_settings.commit()
        
        self.db_lock = asyncio.Lock()
        self.proxies = self.load_proxies()
        self.proxy_queue = deque(self.proxies) if self.proxies else deque([None])
        self.max_workers = min(50, len(self.proxies) if self.proxies else 1)
        self.alliance_tasks = {}
        self.is_running = {}
        self.monitor_started = False
        
        self.control_queue = asyncio.Queue()
        self.control_lock = asyncio.Lock()
        self.current_control = None

    def load_proxies(self):
        proxies = []
        if os.path.exists('proxy.txt'):
            with open('proxy.txt', 'r') as f:
                proxies = [f"http://{line.strip()}" for line in f if line.strip()]
            print(f"{Fore.YELLOW}[INFO] Loaded {len(proxies)} proxies from proxy.txt{Style.RESET_ALL}")
        return proxies

    async def fetch_user_data(self, fid, proxy=None):
        url = 'https://wos-giftcode-api.centurygame.com/api/player'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        current_time = int(time.time() * 1000)
        form = f"fid={fid}&time={current_time}"
        sign = hashlib.md5((form + SECRET).encode('utf-8')).hexdigest()
        form = f"sign={sign}&{form}"

        try:
            connector = ProxyConnector.from_url(proxy) if proxy else None   
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(url, headers=headers, data=form, ssl=False) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        return response.status
        except Exception as e:
            print(f"{Fore.RED}[ERROR] Error fetching data with proxy: {e}{Style.RESET_ALL}")
            return None

    def get_next_proxy(self):
        """Get the next proxy from the rotation queue"""
        if not self.proxy_queue:
            self.proxy_queue = deque(self.proxies) if self.proxies else deque([None])
        
        if self.proxy_queue:
            proxy = self.proxy_queue.popleft()
            self.proxy_queue.append(proxy)  # Put it back at the end for rotation
            return proxy
        return None

    async def process_user(self, fid, old_nickname, old_furnace_lv, old_stove_lv_content, old_kid):
        """Process a single user with a single proxy attempt"""
        proxy = self.get_next_proxy()
        data = await self.fetch_user_data(fid, proxy=proxy)
        return data

    async def process_user_batch(self, users_queue, results):
        """Worker to process users from a queue using available proxies"""
        while not users_queue.empty():
            try:
                user_data = await users_queue.get()
                fid, old_nickname, old_furnace_lv, old_stove_lv_content, old_kid = user_data
                
                data = await self.process_user(fid, old_nickname, old_furnace_lv, old_stove_lv_content, old_kid)
                
                # If we got rate limited or error, try a different proxy up to 3 times
                retry_count = 0
                while (data == 429 or data is None) and retry_count < 3:
                    retry_count += 1
                    await asyncio.sleep(0.5)  # Short delay before retry
                    data = await self.process_user(fid, old_nickname, old_furnace_lv, old_stove_lv_content, old_kid)
                
                results[fid] = {
                    'data': data,
                    'old_data': {
                        'nickname': old_nickname,
                        'furnace_lv': old_furnace_lv,
                        'stove_lv_content': old_stove_lv_content,
                        'kid': old_kid
                    }
                }
                users_queue.task_done()
            except Exception as e:
                print(f"{Fore.RED}[ERROR] Error in worker: {str(e)}{Style.RESET_ALL}")
                users_queue.task_done()

    async def check_agslist(self, channel, alliance_id):
        async with self.db_lock:
            self.cursor_users.execute("SELECT fid, nickname, furnace_lv, stove_lv_content, kid FROM users WHERE alliance = ?", (alliance_id,))
            users = self.cursor_users.fetchall()

            if not users:
                return

        total_users = len(users)
        checked_users = 0

        self.cursor_alliance.execute("SELECT name FROM alliance_list WHERE alliance_id = ?", (alliance_id,))
        alliance_name = self.cursor_alliance.fetchone()[0]

        start_time = datetime.now()
        print(f"{Fore.CYAN}{alliance_name} Alliance Control started at {start_time.strftime('%Y-%m-%d %H:%M:%S')}{Style.RESET_ALL}")
        
        async with self.db_lock:
            with sqlite3.connect('db/settings.sqlite') as settings_db:
                cursor = settings_db.cursor()
                cursor.execute("SELECT value FROM auto LIMIT 1")
                result = cursor.fetchone()
                auto_value = result[0] if result else 1
        
        
        embed = discord.Embed(
            title=f"🏰 {alliance_name} Alliance Control",
            description="🔍 Checking for changes in member status...",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="📊 Status",
            value=f"⏳ Control started at {start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            inline=False
        )
        embed.add_field(
            name="📈 Progress",
            value=f"✨ Members checked: {checked_users}/{total_users}",
            inline=False
        )
        embed.set_footer(text="⚡ Automatic Alliance Control System")
        
        message = None
        if auto_value == 1:
            message = await channel.send(embed=embed)

        furnace_changes, nickname_changes, kid_changes = [], [], []

        # Create a queue for all users
        users_queue = asyncio.Queue()
        results = {}  # Store all results

        # Put all users into the queue
        for user in users:
            await users_queue.put(user)

        # Determine number of workers - use all available proxies, but don't exceed 50 workers
        num_workers = min(self.max_workers, total_users)
        worker_count = max(1, num_workers)  # Ensure at least one worker
        
        embed.description = f"🔍 Checking for changes in member status...\n⚙️ Using {worker_count} workers with available proxies"
        if message:
            await message.edit(embed=embed)
        
        print(f"{Fore.YELLOW}[INFO] Starting {worker_count} worker tasks for {alliance_name}{Style.RESET_ALL}")
        
        # Create and start worker tasks
        workers = []
        for _ in range(worker_count):
            worker = asyncio.create_task(self.process_user_batch(users_queue, results))
            workers.append(worker)
        
        # Update progress
        while not users_queue.empty():
            checked_users = total_users - users_queue.qsize()
            embed.set_field_at(
                1,
                name="📈 Progress",
                value=f"✨ Members checked: {checked_users}/{total_users}",
                inline=False
            )
            if message:
                await message.edit(embed=embed)
            await asyncio.sleep(2)

        # Wait for all workers to complete
        await users_queue.join()
        
        # Cancel all workers
        for worker in workers:
            worker.cancel()
        
        # Process all results and update database
        for fid, result_data in results.items():
            data = result_data['data']
            old_data = result_data['old_data']
            old_nickname = old_data['nickname']
            old_furnace_lv = old_data['furnace_lv']
            old_stove_lv_content = old_data['stove_lv_content']
            old_kid = old_data['kid']
            
            if not isinstance(data, dict):
                continue
                
            user_data = data['data']
            new_furnace_lv = user_data['stove_lv']
            new_nickname = user_data['nickname'].strip()
            new_kid = user_data.get('kid', 0)
            new_stove_lv_content = user_data['stove_lv_content']
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            async with self.db_lock:
                if new_stove_lv_content != old_stove_lv_content:
                    self.cursor_users.execute("UPDATE users SET stove_lv_content = ? WHERE fid = ?", (new_stove_lv_content, fid))
                    self.conn_users.commit()

                if old_kid != new_kid:
                    kid_changes.append(f"👤 **{old_nickname}** has transferred to a new state\n🔄 Old State: `{old_kid}`\n🆕 New State: `{new_kid}`")
                    self.cursor_users.execute("UPDATE users SET kid = ? WHERE fid = ?", (new_kid, fid))
                    self.conn_users.commit()

                if new_furnace_lv != old_furnace_lv:
                    new_furnace_display = level_mapping.get(new_furnace_lv, new_furnace_lv)
                    old_furnace_display = level_mapping.get(old_furnace_lv, old_furnace_lv)
                    self.cursor_changes.execute("INSERT INTO furnace_changes (fid, old_furnace_lv, new_furnace_lv, change_date) VALUES (?, ?, ?, ?)",
                                                 (fid, old_furnace_lv, new_furnace_lv, current_time))
                    self.conn_changes.commit()
                    self.cursor_users.execute("UPDATE users SET furnace_lv = ? WHERE fid = ?", (new_furnace_lv, fid))
                    self.conn_users.commit()
                    furnace_changes.append(f"👤 **{old_nickname}**\n🔥 `{old_furnace_display}` ➡️ `{new_furnace_display}`")

                if new_nickname.lower() != old_nickname.lower().strip():
                    self.cursor_changes.execute("INSERT INTO nickname_changes (fid, old_nickname, new_nickname, change_date) VALUES (?, ?, ?, ?)",
                                                 (fid, old_nickname, new_nickname, current_time))
                    self.conn_changes.commit()
                    self.cursor_users.execute("UPDATE users SET nickname = ? WHERE fid = ?", (new_nickname, fid))
                    self.conn_users.commit()
                    nickname_changes.append(f"📝 `{old_nickname}` ➡️ `{new_nickname}`")

        end_time = datetime.now()
        duration = end_time - start_time

        if furnace_changes or nickname_changes or kid_changes:
            if furnace_changes:
                furnace_embed = discord.Embed(
                    title="🔥 Furnace Level Changes",
                    description="\n\n".join(furnace_changes),
                    color=discord.Color.orange()
                )
                furnace_embed.set_footer(text=f"📊 Total Changes: {len(furnace_changes)}")
                await channel.send(embed=furnace_embed)

            if nickname_changes:
                nickname_embed = discord.Embed(
                    title="📝 Nickname Changes",
                    description="\n".join(nickname_changes),
                    color=discord.Color.blue()
                )
                nickname_embed.set_footer(text=f"📊 Total Changes: {len(nickname_changes)}")
                await channel.send(embed=nickname_embed)

            if kid_changes:
                kid_embed = discord.Embed(
                    title="🌍 State Transfer Notifications",
                    description="\n\n".join(kid_changes),
                    color=discord.Color.green()
                )
                kid_embed.set_footer(text=f"📊 Total Changes: {len(kid_changes)}")
                await channel.send(embed=kid_embed)

            embed.color = discord.Color.green()
            embed.set_field_at(
                0,
                name="📊 Final Status",
                value=f"✅ Control completed with changes\n⏰ {end_time.strftime('%Y-%m-%d %H:%M:%S')}",
                inline=False
            )
            embed.add_field(
                name="⏱️ Duration",
                value=str(duration),
                inline=True
            )
            embed.add_field(
                name="📈 Total Changes",
                value=f"🔄 {len(furnace_changes) + len(nickname_changes) + len(kid_changes)} changes detected",
                inline=True
            )
        else:
            embed.color = discord.Color.green()
            embed.set_field_at(
                0,
                name="📊 Final Status",
                value=f"✅ Control completed successfully\n⏰ {end_time.strftime('%Y-%m-%d %H:%M:%S')}\n📝 No changes detected",
                inline=False
            )
            embed.add_field(
                name="⏱️ Duration",
                value=str(duration),
                inline=True
            )

        if message:
            await message.edit(embed=embed)
        print(f"{Fore.GREEN}{alliance_name} Alliance Control completed at {end_time.strftime('%Y-%m-%d %H:%M:%S')}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}{alliance_name} Alliance Total Duration: {duration}{Style.RESET_ALL}")

    async def send_embed(self, channel, title, description, color):
        embed = discord.Embed(
            title=title,
            description=description,
            color=color
        )
        embed.set_footer(text="🔄 Alliance Control System")
        await channel.send(embed=embed)

    async def process_control_queue(self):
        print("[CONTROL] Queue processor started")
        while True:
            try:
                control_task = await self.control_queue.get()
                channel = control_task['channel']
                alliance_id = control_task['alliance_id']
                is_manual = control_task.get('is_manual', False)
                
                print(f"[CONTROL] Processing alliance ID: {alliance_id} (Manual: {is_manual})")
                
                if self.current_control:
                    await self.current_control
                
                print(f"[CONTROL] Starting control for alliance ID: {alliance_id}")
                
                self.cursor_alliance.execute("""
                    SELECT name FROM alliance_list 
                    WHERE alliance_id = ?
                """, (alliance_id,))
                alliance_name = self.cursor_alliance.fetchone()[0]
                
                self.cursor_users.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (alliance_id,))
                member_count = self.cursor_users.fetchone()[0]
                
                self.current_control = asyncio.create_task(self.check_agslist(channel, alliance_id))
                await self.current_control
                
                self.current_control = None
                self.control_queue.task_done()
                
                print(f"[CONTROL] Completed control for alliance ID: {alliance_id}")
                
                if not is_manual:
                    await asyncio.sleep(60)
                
            except Exception as e:
                print(f"[ERROR] Error in process_control_queue: {str(e)}")
                import traceback
                traceback.print_exc()
                
                error_embed = discord.Embed(
                    title="⚠️ Control Process Error",
                    description=f"An error occurred during the control process:\n```{str(e)}```",
                    color=discord.Color.red()
                )
                try:
                    await channel.send(embed=error_embed)
                except:
                    pass
                
                if not is_manual:
                    await asyncio.sleep(60)

    async def schedule_alliance_check(self, channel, alliance_id, current_interval):
        try:
            await asyncio.sleep(current_interval * 60)
            
            while self.is_running.get(alliance_id, False):
                try:
                    async with self.db_lock:
                        self.cursor_alliance.execute("""
                            SELECT interval 
                            FROM alliancesettings 
                            WHERE alliance_id = ?
                        """, (alliance_id,))
                        result = self.cursor_alliance.fetchone()
                        
                        if not result or result[0] == 0:
                            print(f"[CONTROL] Stopping checks for alliance {alliance_id} - interval disabled")
                            self.is_running[alliance_id] = False
                            break
                        
                        new_interval = result[0]
                        if new_interval != current_interval:
                            print(f"[CONTROL] Interval changed for alliance {alliance_id}: {current_interval} -> {new_interval}")
                            self.is_running[alliance_id] = False
                            self.alliance_tasks[alliance_id] = asyncio.create_task(
                                self.schedule_alliance_check(channel, alliance_id, new_interval)
                            )
                            break

                    await self.control_queue.put({
                        'channel': channel,
                        'alliance_id': alliance_id
                    })
                    
                    await asyncio.sleep(current_interval * 60)
                    
                except Exception as e:
                    print(f"[ERROR] Error in schedule_alliance_check for alliance {alliance_id}: {e}")
                    await asyncio.sleep(60)
                    
        except Exception as e:
            print(f"[ERROR] Fatal error in schedule_alliance_check for alliance {alliance_id}: {e}")
            traceback.print_exc()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.monitor_started:
            print("[CONTROL] Starting monitor and queue processor...")
            self._queue_processor_task = asyncio.create_task(self.process_control_queue())
            self.monitor_alliance_changes.start()
            await self.start_alliance_checks()
            self.monitor_started = True
            print("[CONTROL] Monitor and queue processor started successfully")

    async def start_alliance_checks(self):
        try:
            for task in self.alliance_tasks.values():
                if not task.done():
                    task.cancel()
            self.alliance_tasks.clear()
            self.is_running.clear()

            async with self.db_lock:
                self.cursor_alliance.execute("""
                    SELECT alliance_id, channel_id, interval 
                    FROM alliancesettings
                    WHERE interval > 0
                """)
                alliances = self.cursor_alliance.fetchall()

                if not alliances:
                    print("[CONTROL] No alliances with intervals found")
                    return

                print(f"[CONTROL] Found {len(alliances)} alliances with intervals")
                
                for alliance_id, channel_id, interval in alliances:
                    channel = self.bot.get_channel(channel_id)
                    if channel is not None:
                        print(f"[CONTROL] Starting initial check for alliance {alliance_id}")
                        await self.control_queue.put({
                            'channel': channel,
                            'alliance_id': alliance_id
                        })
                        
                        self.is_running[alliance_id] = True
                        self.alliance_tasks[alliance_id] = asyncio.create_task(
                            self.schedule_alliance_check(channel, alliance_id, interval)
                        )
                        
                        await asyncio.sleep(2)
                    else:
                        print(f"[CONTROL] Channel not found for alliance {alliance_id}")

        except Exception as e:
            print(f"[ERROR] Error in start_alliance_checks: {e}")
            traceback.print_exc()

    async def cog_load(self):
        try:
            print("[MONITOR] Cog loaded successfully")
        except Exception as e:
            print(f"[ERROR] Error in cog_load: {e}")
            import traceback
            print(traceback.format_exc())

    @tasks.loop(minutes=1)
    async def monitor_alliance_changes(self):
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            async with self.db_lock:
                self.cursor_alliance.execute("SELECT alliance_id, channel_id, interval FROM alliancesettings")
                current_settings = {
                    alliance_id: (channel_id, interval)
                    for alliance_id, channel_id, interval in self.cursor_alliance.fetchall()
                }

                for alliance_id, (channel_id, interval) in current_settings.items():
                    task_exists = alliance_id in self.alliance_tasks
                    
                    if interval == 0 and task_exists:
                        self.is_running[alliance_id] = False
                        if not self.alliance_tasks[alliance_id].done():
                            self.alliance_tasks[alliance_id].cancel()
                        del self.alliance_tasks[alliance_id]
                        continue

                    if interval > 0 and (not task_exists or self.alliance_tasks[alliance_id].done()):
                        channel = self.bot.get_channel(channel_id)
                        if channel is not None:
                            self.is_running[alliance_id] = True
                            self.alliance_tasks[alliance_id] = asyncio.create_task(
                                self.schedule_alliance_check(channel, alliance_id, interval)
                            )

                for alliance_id in list(self.alliance_tasks.keys()):
                    if alliance_id not in current_settings:
                        self.is_running[alliance_id] = False
                        if not self.alliance_tasks[alliance_id].done():
                            self.alliance_tasks[alliance_id].cancel()
                        del self.alliance_tasks[alliance_id]

        except Exception as e:
            print(f"[ERROR] Error in monitor_alliance_changes: {e}")
            import traceback
            print(traceback.format_exc())

    @monitor_alliance_changes.before_loop
    async def before_monitor_alliance_changes(self):
        await self.bot.wait_until_ready()

    @monitor_alliance_changes.after_loop
    async def after_monitor_alliance_changes(self):
        if self.monitor_alliance_changes.failed():
            print(Fore.RED + "Monitor alliance changes task failed. Restarting..." + Style.RESET_ALL)
            self.monitor_alliance_changes.restart()

async def setup(bot):
    control_cog = Control(bot)
    await bot.add_cog(control_cog)
    if not control_cog.monitor_alliance_changes.is_running():
        control_cog.monitor_alliance_changes.start()
