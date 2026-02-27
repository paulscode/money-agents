"""
Disclaimer service.

Manages the disclaimer acknowledgement flow:
- All users must see the disclaimer on login (unless they opted out via checkbox)
- The initial admin must acknowledge before agents can be enabled
- Factory reset returns to initial state (agents disabled)
"""
import logging
from datetime import datetime
from app.core.datetime_utils import utc_now

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole, SystemSetting
from app.models.agent_scheduler import AgentDefinition

logger = logging.getLogger(__name__)

# The disclaimer text shown to all users
DISCLAIMER_TEXT = """IMPORTANT — PLEASE READ CAREFULLY BEFORE PROCEEDING

This software is experimental and is provided on an "as is" basis, without warranties or guarantees of any kind, whether express or implied.

By acknowledging this disclaimer, you confirm that you understand and accept the following:

1. EXPERIMENTAL SOFTWARE — Money Agents is experimental software under active development. Features may be incomplete, contain bugs, or behave unpredictably. The autonomous agents may take actions that result in unintended consequences.

2. NO GUARANTEE OF PROFIT — There is no guarantee that any campaigns, strategies, or automations will generate revenue, profit, or any positive financial outcome. There is a high probability that campaigns will fail, and any budgets allocated to campaigns may be lost entirely.

3. RISK OF FINANCIAL LOSS — Connecting an LND (Lightning Network Daemon) node to this software exposes all funds held by that node — including on-chain wallet balances and funds committed to Lightning payment channels — to the risk of partial or total loss. Loss of funds may result from, but is not limited to: autonomous agent actions (such as initiating payments, paying invoices, opening or closing channels, or transferring funds); software bugs or defects; security vulnerabilities; exposure or compromise of authentication credentials (such as macaroons); incorrect or malformed destination addresses; network failures; user error; or any other cause whether foreseen or unforeseen. Once funds are spent or transferred, they cannot be recovered. You should only connect a node containing funds that you can afford to lose completely.

4. COLD STORAGE TRANSFER RISK — Automated transfer to cold storage operations (reverse submarine swaps) involve third-party services and carry additional risk of fund loss due to timing, network conditions, or service failures.

5. API KEY AND CREDENTIAL EXPOSURE — This software stores third-party API keys and service credentials (such as LLM provider keys, LND macaroons, and other authentication tokens) in its configuration and environment. If these credentials are exposed — whether through software vulnerabilities, misconfiguration, unauthorized access to the host system, compromised backups, log files, or any other means — third parties may use them to incur charges on your accounts, drain associated funds, or access your services without authorization. You are solely responsible for safeguarding your credentials, rotating keys if compromise is suspected, and monitoring your third-party accounts for unauthorized usage. The authors assume no responsibility for financial losses or charges resulting from credential exposure, regardless of cause.

6. NO LIABILITY — The authors, contributors, and maintainers of Money Agents shall not be held responsible or liable for any loss of funds, loss of data, loss of revenue, or any direct, indirect, incidental, special, consequential, or punitive damages arising from the use of this software.

7. NO LIABILITY FOR DAMAGES — The authors shall not be held responsible for any physical damage to hardware, data corruption, or system instability caused by or related to the operation of this software, including but not limited to GPU utilization, disk I/O, network operations, or Docker container activity.

8. USE AT YOUR OWN RISK — By proceeding, you acknowledge that you are using this software entirely at your own risk and that you have read and understood the terms above.

This disclaimer applies to all users of this installation."""


async def get_system_setting(db: AsyncSession, key: str) -> str | None:
    """Get a system setting value by key."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def set_system_setting(db: AsyncSession, key: str, value: str) -> None:
    """Set a system setting value. Creates it if it doesn't exist."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
        setting.updated_at = utc_now()
    else:
        db.add(SystemSetting(key=key, value=value, updated_at=utc_now()))


async def are_agents_enabled(db: AsyncSession) -> bool:
    """Check if agents are globally enabled (admin has acknowledged disclaimer)."""
    value = await get_system_setting(db, "agents_enabled")
    return value == "true"


async def get_disclaimer_status(db: AsyncSession, user: User) -> dict:
    """
    Get the disclaimer status for a user.
    
    Returns dict with:
    - requires_disclaimer: whether the user needs to see/acknowledge the disclaimer
    - disclaimer_text: the disclaimer text
    - is_initial_admin: whether this is the initial admin first-acknowledgement scenario
    - agents_enabled: whether agents are currently enabled
    - acknowledged_at: timestamp of user's last acknowledgement (if any)
    - show_on_login: whether the user has opted to see it on every login
    """
    agents_enabled = await are_agents_enabled(db)
    is_admin = user.role == UserRole.ADMIN.value
    
    # Determine if this is the initial admin scenario (agents not yet enabled)
    is_initial_admin = is_admin and not agents_enabled
    
    # User must see disclaimer if:
    # 1. They have never acknowledged it, OR
    # 2. They have show_disclaimer_on_login = True
    never_acknowledged = user.disclaimer_acknowledged_at is None
    show_on_login = user.show_disclaimer_on_login
    
    requires_disclaimer = never_acknowledged or show_on_login
    
    return {
        "requires_disclaimer": requires_disclaimer,
        "disclaimer_text": DISCLAIMER_TEXT,
        "is_initial_admin": is_initial_admin,
        "agents_enabled": agents_enabled,
        "acknowledged_at": user.disclaimer_acknowledged_at.isoformat() if user.disclaimer_acknowledged_at else None,
        "show_on_login": show_on_login,
    }


async def acknowledge_disclaimer(
    db: AsyncSession, 
    user: User, 
    show_on_login: bool = True
) -> dict:
    """
    Record the user's disclaimer acknowledgement.
    
    If this is the first admin acknowledgement and agents are not yet enabled,
    this will enable all agents and start normal scheduling.
    
    Returns dict with the result.
    """
    now = utc_now()
    agents_were_enabled = await are_agents_enabled(db)
    is_admin = user.role == UserRole.ADMIN.value
    
    # Record acknowledgement
    user.disclaimer_acknowledged_at = now
    user.show_disclaimer_on_login = show_on_login
    
    agents_just_enabled = False
    
    # If this is an admin and agents haven't been enabled yet, enable them now
    if is_admin and not agents_were_enabled:
        logger.info(
            f"First admin acknowledgement by user {user.username} (id={user.id}). "
            f"Enabling all agents."
        )
        await set_system_setting(db, "agents_enabled", "true")
        
        # Enable all agent definitions and set them to IDLE with a next_run
        result = await db.execute(select(AgentDefinition))
        agents = result.scalars().all()
        for agent in agents:
            if not agent.is_enabled:
                agent.is_enabled = True
                agent.status = "idle"
                if agent.schedule_interval_seconds:
                    from datetime import timedelta
                    agent.next_run_at = now + timedelta(seconds=agent.schedule_interval_seconds)
                logger.info(f"  Enabled agent: {agent.slug}")
        
        agents_just_enabled = True
    
    await db.commit()
    await db.refresh(user)
    
    result = {
        "acknowledged": True,
        "acknowledged_at": now.isoformat(),
        "show_on_login": show_on_login,
        "agents_enabled": agents_just_enabled or agents_were_enabled,
        "agents_just_enabled": agents_just_enabled,
    }
    
    if agents_just_enabled:
        logger.info("Agents have been globally enabled following first admin acknowledgement.")
    
    return result


async def ensure_agents_disabled_on_fresh_install(db: AsyncSession) -> None:
    """
    Called during startup to ensure agents start disabled on a fresh install.
    
    If the 'agents_enabled' setting doesn't exist or is 'false', 
    all agent definitions should have is_enabled=False.
    
    This is idempotent — once agents_enabled='true' (after first admin ack),
    this function does nothing.
    """
    agents_enabled = await are_agents_enabled(db)
    if agents_enabled:
        # Agents have been enabled by admin acknowledgement — do nothing
        return
    
    # Fresh install or post-factory-reset: ensure all agents are disabled
    result = await db.execute(
        select(AgentDefinition).where(AgentDefinition.is_enabled == True)
    )
    enabled_agents = result.scalars().all()
    
    if enabled_agents:
        for agent in enabled_agents:
            agent.is_enabled = False
            agent.status = "idle"
            agent.next_run_at = None
            logger.info(f"Startup: Disabled agent '{agent.slug}' (awaiting admin disclaimer acknowledgement)")
        await db.commit()
