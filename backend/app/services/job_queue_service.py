"""Job queue service for managing resource-locked tool executions."""
import logging
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import List, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resource import JobQueue, Resource
from app.schemas.resource import JobStatus, ResourceStatus
from app.services import resource_service

logger = logging.getLogger(__name__)


async def create_job(
    db: AsyncSession,
    tool_id: UUID,
    resource_id: UUID,
    conversation_id: UUID,
    message_id: Optional[UUID] = None,
    parameters: Optional[dict] = None,
    expected_duration_minutes: Optional[int] = None
) -> JobQueue:
    """
    Create a new job in the queue.
    
    Args:
        db: Database session
        tool_id: ID of the tool to execute
        resource_id: ID of the required resource
        conversation_id: ID of the conversation
        message_id: Optional message ID
        parameters: Tool execution parameters
        expected_duration_minutes: Expected job duration in minutes.
            Used for staleness detection during crash recovery.
            If not set, a default threshold is used.
        
    Returns:
        Created job
    """
    job = JobQueue(
        id=uuid4(),
        tool_id=tool_id,
        resource_id=resource_id,
        conversation_id=conversation_id,
        message_id=message_id,
        status=JobStatus.QUEUED,
        parameters=parameters or {},
        expected_duration_minutes=expected_duration_minutes
    )
    
    db.add(job)
    await db.commit()
    await db.refresh(job)
    
    logger.info(f"Created job {job.id} for tool {tool_id} on resource {resource_id}")
    
    # Try to process queue for this resource
    await process_resource_queue(db, resource_id)
    
    return job


async def get_job(db: AsyncSession, job_id: UUID) -> Optional[JobQueue]:
    """Get a job by ID."""
    result = await db.execute(
        select(JobQueue).where(JobQueue.id == job_id)
    )
    return result.scalar_one_or_none()


async def get_resource_queue(db: AsyncSession, resource_id: UUID) -> List[JobQueue]:
    """Get all queued and running jobs for a resource, ordered by queue time."""
    result = await db.execute(
        select(JobQueue)
        .where(JobQueue.resource_id == resource_id)
        .where(JobQueue.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        .order_by(JobQueue.queued_at)
    )
    return list(result.scalars().all())


async def get_next_queued_job(db: AsyncSession, resource_id: UUID) -> Optional[JobQueue]:
    """Get the next queued job for a resource (FIFO)."""
    result = await db.execute(
        select(JobQueue)
        .where(JobQueue.resource_id == resource_id)
        .where(JobQueue.status == JobStatus.QUEUED)
        .order_by(JobQueue.queued_at)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_running_job(db: AsyncSession, resource_id: UUID) -> Optional[JobQueue]:
    """Get the currently running job for a resource (should only be one)."""
    result = await db.execute(
        select(JobQueue)
        .where(JobQueue.resource_id == resource_id)
        .where(JobQueue.status == JobStatus.RUNNING)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_all_running_jobs_for_resource(db: AsyncSession, resource_id: UUID) -> List[JobQueue]:
    """Get ALL running jobs for a resource (for stacking detection - should normally be 0 or 1)."""
    result = await db.execute(
        select(JobQueue)
        .where(JobQueue.resource_id == resource_id)
        .where(JobQueue.status == JobStatus.RUNNING)
    )
    return list(result.scalars().all())


async def start_job(db: AsyncSession, job_id: UUID) -> bool:
    """
    Mark a job as running and acquire the resource.
    
    Includes stacking prevention: checks if resource already has running jobs.
    
    Returns:
        True if job started successfully, False otherwise.
    """
    job = await get_job(db, job_id)
    if not job or job.status != JobStatus.QUEUED:
        return False
    
    # STACKING PREVENTION: Check if resource already has running jobs
    existing_running = await get_all_running_jobs_for_resource(db, job.resource_id)
    if existing_running:
        logger.warning(
            f"POTENTIAL STACKING: Attempting to start job {job_id} but resource {job.resource_id} "
            f"already has {len(existing_running)} running job(s): {[str(j.id) for j in existing_running]}"
        )
        # Don't start the job - resource is actually in use
        return False
    
    # Check if resource is available
    if not await resource_service.acquire_resource(db, job.resource_id):
        logger.warning(f"Cannot start job {job_id} - resource {job.resource_id} not available")
        return False
    
    job.status = JobStatus.RUNNING
    job.started_at = utc_now()
    await db.commit()
    
    logger.info(f"Started job {job_id} on resource {job.resource_id}")
    return True


async def complete_job(
    db: AsyncSession,
    job_id: UUID,
    result: Optional[dict] = None,
    error: Optional[str] = None
) -> bool:
    """
    Mark a job as completed or failed and release the resource.
    
    Args:
        db: Database session
        job_id: Job ID
        result: Job result if successful
        error: Error message if failed
        
    Returns:
        True if job completed successfully.
    """
    job = await get_job(db, job_id)
    if not job or job.status != JobStatus.RUNNING:
        return False
    
    job.status = JobStatus.COMPLETED if not error else JobStatus.FAILED
    job.result = result
    job.error = error
    job.completed_at = utc_now()
    
    # Release the resource
    await resource_service.release_resource(db, job.resource_id)
    
    await db.commit()
    
    logger.info(f"Completed job {job_id} with status {job.status}")
    
    # Try to start the next job in the queue
    await process_resource_queue(db, job.resource_id)
    
    return True


async def cancel_job(db: AsyncSession, job_id: UUID) -> bool:
    """
    Cancel a queued job.
    
    Returns:
        True if cancelled successfully, False if job is already running or completed.
    """
    job = await get_job(db, job_id)
    if not job or job.status != JobStatus.QUEUED:
        return False
    
    job.status = JobStatus.CANCELLED
    job.completed_at = utc_now()
    await db.commit()
    
    logger.info(f"Cancelled job {job_id}")
    return True


async def process_resource_queue(db: AsyncSession, resource_id: UUID) -> bool:
    """
    Process the queue for a resource - start the next job if resource is available.
    
    Returns:
        True if a job was started, False otherwise.
    """
    # Check if there's already a running job
    running_job = await get_running_job(db, resource_id)
    if running_job:
        logger.debug(f"Resource {resource_id} already has a running job: {running_job.id}")
        return False
    
    # Check if resource is available
    resource = await resource_service.get_resource(db, resource_id)
    if not resource or resource.status != ResourceStatus.AVAILABLE:
        logger.debug(f"Resource {resource_id} not available (status: {resource.status if resource else 'not found'})")
        return False
    
    # Get next queued job
    next_job = await get_next_queued_job(db, resource_id)
    if not next_job:
        logger.debug(f"No queued jobs for resource {resource_id}")
        return False
    
    # Start the job
    success = await start_job(db, next_job.id)
    if success:
        logger.info(f"Started next job {next_job.id} from queue for resource {resource_id}")
    
    return success


async def get_conversation_jobs(db: AsyncSession, conversation_id: UUID) -> List[JobQueue]:
    """Get all jobs for a conversation."""
    result = await db.execute(
        select(JobQueue)
        .where(JobQueue.conversation_id == conversation_id)
        .order_by(JobQueue.queued_at.desc())
    )
    return list(result.scalars().all())


# =============================================================================
# Startup / Recovery Functions
# =============================================================================

# Default staleness thresholds (minutes)
DEFAULT_JOB_STALE_THRESHOLD = 30  # Jobs without expected_duration
DEFAULT_QUEUE_STALE_THRESHOLD = 30  # Queued jobs waiting too long
STALENESS_PADDING_FACTOR = 1.5  # Multiply expected_duration by this for threshold


async def recover_stale_jobs(
    db: AsyncSession,
    default_threshold_minutes: int = DEFAULT_JOB_STALE_THRESHOLD
) -> dict:
    """
    Recover from stale jobs after system restart or crash.
    
    This should be called on application startup. It:
    1. Fails any jobs stuck in 'running' status (process died)
    2. Fails any jobs stuck in 'queued' too long (orphaned)
    3. Releases resources that were stuck in 'in_use'
    
    Staleness is determined per-job:
    - If job has expected_duration_minutes, use that * STALENESS_PADDING_FACTOR
    - Otherwise, use default_threshold_minutes
    
    Args:
        db: Database session
        default_threshold_minutes: Minutes after which a job without expected_duration is considered stuck
        
    Returns:
        Dict with recovery statistics
    """
    from app.schemas.resource import JobStatus, ResourceStatus
    from datetime import timezone
    
    result = {
        "stale_running_jobs": 0,
        "stale_queued_jobs": 0,
        "resources_released": 0,
        "details": []
    }
    
    now = utc_now()
    
    # 1. Find and fail stuck "running" jobs
    # Get ALL running jobs, then check each one's threshold individually
    running_result = await db.execute(
        select(JobQueue).where(JobQueue.status == JobStatus.RUNNING)
    )
    running_jobs = list(running_result.scalars().all())
    
    for job in running_jobs:
        # Calculate threshold for this specific job
        if job.expected_duration_minutes:
            threshold_minutes = int(job.expected_duration_minutes * STALENESS_PADDING_FACTOR)
        else:
            threshold_minutes = default_threshold_minutes
        
        threshold_time = now - timedelta(minutes=threshold_minutes)
        
        # Ensure started_at is timezone-aware for comparison
        started_at = ensure_utc(job.started_at)
        
        if started_at and started_at < threshold_time:
            runtime_minutes = (now - started_at).total_seconds() / 60
            job.status = JobStatus.FAILED
            job.error = f"SYSTEM_RECOVERY: Job exceeded expected duration (expected ~{job.expected_duration_minutes or default_threshold_minutes} min, ran {runtime_minutes:.1f} min)"
            job.completed_at = now
            result["stale_running_jobs"] += 1
            result["details"].append(f"Failed stale running job {job.id} (ran {runtime_minutes:.1f} min, threshold {threshold_minutes} min)")
            logger.warning(f"Recovery: Failed stale running job {job.id} (ran {runtime_minutes:.1f} min, threshold {threshold_minutes} min)")
    
    # 2. Find and fail orphaned "queued" jobs (queued for too long)
    # These might have been queued when a resource was available but then
    # the worker died before starting them
    # Use default threshold for queued jobs (they don't have expected_duration yet)
    queue_threshold_time = now - timedelta(minutes=DEFAULT_QUEUE_STALE_THRESHOLD)
    
    queued_result = await db.execute(
        select(JobQueue)
        .where(JobQueue.status == JobStatus.QUEUED)
    )
    queued_jobs = list(queued_result.scalars().all())
    
    for job in queued_jobs:
        # Ensure queued_at is timezone-aware
        queued_at = ensure_utc(job.queued_at)
        
        if queued_at and queued_at < queue_threshold_time:
            wait_time_minutes = (now - queued_at).total_seconds() / 60
            job.status = JobStatus.FAILED
            job.error = f"SYSTEM_RECOVERY: Job was queued too long ({wait_time_minutes:.1f} min), likely orphaned"
            job.completed_at = now
            result["stale_queued_jobs"] += 1
            result["details"].append(f"Failed orphaned queued job {job.id} (waited {wait_time_minutes:.1f} min)")
            logger.warning(f"Recovery: Failed orphaned queued job {job.id} (queued {job.queued_at})")
    
    # Flush changes so the resource check sees updated job statuses
    if result["stale_running_jobs"] or result["stale_queued_jobs"]:
        await db.flush()
    
    # 3. Release any resources stuck in 'in_use' with no running job
    from app.models.resource import Resource
    resources_result = await db.execute(
        select(Resource).where(Resource.status == ResourceStatus.IN_USE)
    )
    in_use_resources = list(resources_result.scalars().all())
    
    for resource in in_use_resources:
        # Check if there's actually a running job for this resource
        running_job = await get_running_job(db, resource.id)
        if not running_job:
            # Resource is in_use but no running job - release it
            resource.status = ResourceStatus.AVAILABLE
            result["resources_released"] += 1
            result["details"].append(f"Released orphaned resource {resource.name}")
            logger.warning(f"Recovery: Released orphaned resource {resource.name} (was in_use with no job)")
    
    if any([result["stale_running_jobs"], result["stale_queued_jobs"], result["resources_released"]]):
        await db.commit()
        logger.info(f"System recovery completed: {result}")
    else:
        logger.info("System recovery: No stale records found")
    
    return result


async def get_system_health_status(db: AsyncSession) -> dict:
    """
    Get current health status of the job queue system.
    
    Returns counts of jobs in each status and any anomalies.
    Detects potential stacking issues from incorrect recovery.
    """
    from app.schemas.resource import JobStatus, ResourceStatus
    from app.models.resource import Resource
    from app.models.agent_scheduler import AgentRun, AgentRunStatus, AgentDefinition, AgentStatus
    from sqlalchemy import func
    from datetime import timezone
    
    now = utc_now()
    
    # Job counts by status
    job_counts = {}
    for status in [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]:
        count_result = await db.execute(
            select(func.count(JobQueue.id)).where(JobQueue.status == status)
        )
        job_counts[status] = count_result.scalar() or 0
    
    # Resource counts by status
    resource_counts = {}
    for status in [ResourceStatus.AVAILABLE, ResourceStatus.IN_USE, ResourceStatus.MAINTENANCE, ResourceStatus.DISABLED]:
        count_result = await db.execute(
            select(func.count(Resource.id)).where(Resource.status == status)
        )
        resource_counts[status] = count_result.scalar() or 0
    
    # Check for anomalies
    anomalies = []
    critical_anomalies = []  # Stacking issues that need immediate attention
    
    # =================================================================
    # STACKING DETECTION - Critical anomalies from incorrect recovery
    # =================================================================
    
    # 1. Multiple running jobs on same resource (JOB STACKING)
    running_jobs_result = await db.execute(
        select(JobQueue).where(JobQueue.status == JobStatus.RUNNING)
    )
    running_jobs = list(running_jobs_result.scalars().all())
    
    # Group by resource_id
    jobs_by_resource = {}
    for job in running_jobs:
        if job.resource_id not in jobs_by_resource:
            jobs_by_resource[job.resource_id] = []
        jobs_by_resource[job.resource_id].append(job)
    
    for resource_id, jobs in jobs_by_resource.items():
        if len(jobs) > 1:
            # Get resource name
            res_result = await db.execute(select(Resource).where(Resource.id == resource_id))
            resource = res_result.scalar_one_or_none()
            resource_name = resource.name if resource else str(resource_id)
            
            job_ids = [str(j.id)[:8] for j in jobs]
            critical_anomalies.append({
                "type": "JOB_STACKING",
                "severity": "critical",
                "message": f"Multiple running jobs ({len(jobs)}) on resource '{resource_name}'",
                "details": {
                    "resource_id": str(resource_id),
                    "resource_name": resource_name,
                    "job_ids": [str(j.id) for j in jobs],
                    "started_times": [j.started_at.isoformat() if j.started_at else None for j in jobs]
                }
            })
            logger.error(f"STACKING DETECTED: {len(jobs)} running jobs on resource '{resource_name}': {job_ids}")
    
    # 2. Multiple concurrent agent runs for same agent (AGENT STACKING)
    running_runs_result = await db.execute(
        select(AgentRun).where(AgentRun.status == AgentRunStatus.RUNNING)
    )
    running_runs = list(running_runs_result.scalars().all())
    
    # Group by agent_id
    runs_by_agent = {}
    for run in running_runs:
        if run.agent_id not in runs_by_agent:
            runs_by_agent[run.agent_id] = []
        runs_by_agent[run.agent_id].append(run)
    
    for agent_id, runs in runs_by_agent.items():
        if len(runs) > 1:
            # Get agent name
            agent_result = await db.execute(select(AgentDefinition).where(AgentDefinition.id == agent_id))
            agent = agent_result.scalar_one_or_none()
            agent_name = agent.slug if agent else str(agent_id)
            
            run_ids = [str(r.id)[:8] for r in runs]
            critical_anomalies.append({
                "type": "AGENT_STACKING",
                "severity": "critical",
                "message": f"Multiple concurrent runs ({len(runs)}) for agent '{agent_name}'",
                "details": {
                    "agent_id": str(agent_id),
                    "agent_slug": agent_name,
                    "run_ids": [str(r.id) for r in runs],
                    "started_times": [r.started_at.isoformat() if r.started_at else None for r in runs]
                }
            })
            logger.error(f"STACKING DETECTED: {len(runs)} concurrent runs for agent '{agent_name}': {run_ids}")
    
    # 3. Agent status is IDLE but has running agent_runs
    agents_result = await db.execute(
        select(AgentDefinition).where(AgentDefinition.status == AgentStatus.IDLE)
    )
    idle_agents = list(agents_result.scalars().all())
    
    for agent in idle_agents:
        # Check if this agent has any running runs
        running_for_agent = [r for r in running_runs if r.agent_id == agent.id]
        if running_for_agent:
            critical_anomalies.append({
                "type": "STATUS_MISMATCH",
                "severity": "warning",
                "message": f"Agent '{agent.slug}' is IDLE but has {len(running_for_agent)} running agent_run(s)",
                "details": {
                    "agent_slug": agent.slug,
                    "agent_status": "idle",
                    "running_run_ids": [str(r.id) for r in running_for_agent]
                }
            })
            logger.warning(f"STATUS MISMATCH: Agent '{agent.slug}' is IDLE but has running runs")
    
    # =================================================================
    # Standard anomaly checks
    # =================================================================
    
    # Anomaly: in_use resources without running jobs
    for_result = await db.execute(
        select(Resource).where(Resource.status == ResourceStatus.IN_USE)
    )
    in_use_resources = list(for_result.scalars().all())
    for resource in in_use_resources:
        running_job = await get_running_job(db, resource.id)
        if not running_job:
            anomalies.append(f"Resource '{resource.name}' is in_use but has no running job")
    
    # Anomaly: very old running jobs (use expected_duration if available)
    for job in running_jobs:
        if job.started_at:
            started_at = ensure_utc(job.started_at)
            
            runtime_minutes = (now - started_at).total_seconds() / 60
            threshold = (job.expected_duration_minutes or 30) * STALENESS_PADDING_FACTOR
            
            if runtime_minutes > threshold:
                anomalies.append(
                    f"Job {str(job.id)[:8]} has been running for {runtime_minutes:.1f} min "
                    f"(expected ~{job.expected_duration_minutes or 30} min)"
                )
    
    return {
        "job_counts": job_counts,
        "resource_counts": resource_counts,
        "anomalies": anomalies,
        "critical_anomalies": critical_anomalies,
        "healthy": len(anomalies) == 0 and len(critical_anomalies) == 0,
        "has_stacking": len(critical_anomalies) > 0
    }


async def cancel_queued_jobs_for_resource(
    db: AsyncSession,
    resource_id: UUID,
    reason: str = "Resource taken offline"
) -> int:
    """
    Cancel all queued (not yet running) jobs for a resource.
    
    Called when a resource is put into maintenance or disabled.
    Running jobs are NOT cancelled - they're allowed to complete.
    
    Args:
        db: Database session
        resource_id: Resource ID
        reason: Cancellation reason for error message
        
    Returns:
        Number of jobs cancelled
    """
    # Get all queued jobs for this resource
    result = await db.execute(
        select(JobQueue)
        .where(JobQueue.resource_id == resource_id)
        .where(JobQueue.status == JobStatus.QUEUED)
    )
    queued_jobs = list(result.scalars().all())
    
    count = 0
    for job in queued_jobs:
        job.status = JobStatus.CANCELLED
        job.error = f"RESOURCE_OFFLINE: {reason}"
        job.completed_at = utc_now()
        count += 1
    
    if count > 0:
        await db.commit()
        logger.info(f"Cancelled {count} queued jobs for resource {resource_id}: {reason}")
    
    return count


async def force_complete_running_job(
    db: AsyncSession,
    resource_id: UUID,
    error_message: str = "Resource forcibly taken offline"
) -> Optional[UUID]:
    """
    Force-complete a running job when resource is being disabled.
    
    This marks the job as FAILED and releases the resource.
    The actual process may still be running - the caller is responsible
    for any process termination if needed.
    
    Args:
        db: Database session
        resource_id: Resource ID
        error_message: Error to record
        
    Returns:
        Job ID if a running job was terminated, None otherwise
    """
    running_job = await get_running_job(db, resource_id)
    if not running_job:
        return None
    
    running_job.status = JobStatus.FAILED
    running_job.error = f"RESOURCE_OFFLINE: {error_message}"
    running_job.completed_at = utc_now()
    
    # Release the resource
    await resource_service.release_resource(db, resource_id)
    
    await db.commit()
    logger.warning(f"Force-completed running job {running_job.id} for resource {resource_id}")
    
    return running_job.id


async def cleanup_resource_jobs(
    db: AsyncSession,
    resource_id: UUID,
    force_stop_running: bool = False,
    reason: str = "Resource maintenance"
) -> dict:
    """
    Clean up all jobs when a resource goes offline.
    
    This is the main entry point for resource offline handling.
    
    Args:
        db: Database session
        resource_id: Resource ID
        force_stop_running: If True, also force-fail running jobs.
                           If False, running jobs are allowed to complete.
        reason: Reason for cleanup (shown in job error messages)
        
    Returns:
        Dict with 'queued_cancelled', 'running_terminated' counts
    """
    result = {
        "queued_cancelled": 0,
        "running_terminated": None,
    }
    
    # Always cancel queued jobs
    result["queued_cancelled"] = await cancel_queued_jobs_for_resource(
        db, resource_id, reason
    )
    
    # Optionally terminate running job
    if force_stop_running:
        terminated_job_id = await force_complete_running_job(
            db, resource_id, reason
        )
        result["running_terminated"] = str(terminated_job_id) if terminated_job_id else None
    
    return result
