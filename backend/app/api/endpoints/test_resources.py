"""Testing endpoints for resource management system."""
from typing import Optional
from uuid import UUID
import asyncio

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_db
from app.models import User
from app.services import job_queue_service, resource_service

router = APIRouter()


async def simulate_job_execution(job_id: str, duration: int, db_session):
    """Simulate a job execution by sleeping."""
    await asyncio.sleep(duration)
    # In a real scenario, the job would update itself when complete
    # For testing, we'll just let it timeout or manually complete


@router.post("/simulate-load", status_code=201)
async def simulate_resource_load(
    resource_id: UUID,
    num_jobs: int = 5,
    job_duration: int = 10,
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> dict:
    """
    Create dummy jobs for testing the queue system (Admin only).
    
    Args:
        resource_id: UUID of the resource to test
        num_jobs: Number of test jobs to create (default: 5, max: 50)
        job_duration: How long each job should run in seconds (default: 10)
    
    Returns:
        Dictionary with created job count and job IDs
    """
    from sqlalchemy import select
    from app.models import Tool
    
    # Validate resource exists
    resource = await resource_service.get_resource(db, resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    
    # Limit to prevent abuse
    num_jobs = min(num_jobs, 50)
    job_duration = min(job_duration, 300)  # Max 5 minutes per job
    
    # Get a real tool to associate with test jobs (or use first available)
    result = await db.execute(select(Tool).limit(1))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=400, detail="No tools available. Please create at least one tool first.")
    
    # Create dummy jobs
    job_ids = []
    for i in range(num_jobs):
        job = await job_queue_service.create_job(
            db=db,
            tool_id=str(tool.id),  # Use a real tool ID
            resource_id=resource_id,
            parameters={
                "test": True,
                "job_number": i + 1,
                "duration": job_duration,
                "description": f"Test job {i + 1}/{num_jobs}"
            },
            conversation_id=None,
            message_id=None
        )
        job_ids.append(str(job.id))
    
    # Start processing the queue (non-blocking)
    # In production, this would be handled by a background worker
    # For testing, we'll just mark the first job as started
    if num_jobs > 0:
        first_job_id = job_ids[0]
        await job_queue_service.start_job(db, first_job_id)
    
    return {
        "message": f"Created {num_jobs} test job(s) for resource {resource.name}",
        "resource_id": str(resource_id),
        "resource_name": resource.name,
        "num_jobs": num_jobs,
        "job_duration": job_duration,
        "job_ids": job_ids,
        "note": "Jobs are queued. Complete them manually using the complete-job endpoint or they will timeout."
    }


@router.post("/complete-job/{job_id}")
async def complete_test_job(
    job_id: UUID,
    success: bool = True,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> dict:
    """
    Manually complete a test job (Admin only).
    
    This allows testing the queue progression without waiting for jobs to timeout.
    """
    result = {"test_completed": True} if success else None
    error = None if success else "Test job intentionally failed"
    
    await job_queue_service.complete_job(
        db=db,
        job_id=str(job_id),
        result=result,
        error=error
    )
    
    return {
        "message": f"Job {job_id} marked as {'completed' if success else 'failed'}",
        "job_id": str(job_id),
        "success": success
    }


@router.delete("/clear-test-jobs/{resource_id}")
async def clear_test_jobs(
    resource_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> dict:
    """
    Clear all test jobs for a resource (Admin only).
    
    Removes all jobs with test=True in parameters.
    """
    from sqlalchemy import delete, select
    from app.models.resource import JobQueue
    
    # Find test jobs
    result = await db.execute(
        select(JobQueue).where(
            JobQueue.resource_id == resource_id,
            JobQueue.parameters["test"].astext == "true"
        )
    )
    test_jobs = result.scalars().all()
    
    # Delete them
    for job in test_jobs:
        await db.delete(job)
    
    await db.commit()
    
    return {
        "message": f"Cleared {len(test_jobs)} test job(s)",
        "resource_id": str(resource_id),
        "deleted_count": len(test_jobs)
    }
