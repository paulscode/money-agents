#!/usr/bin/env python3
"""
Resource Agent - Main entry point.

A cross-platform agent for distributed job execution.
Connects to the Money Agents broker and executes assigned jobs.
"""
import asyncio
import logging
import signal
import sys
from pathlib import Path

import structlog

from config import load_config, Config
from capabilities import detect_capabilities
from broker_client import BrokerClient
from executor import JobExecutor


def setup_logging(config: Config):
    """Configure structured logging."""
    level = getattr(logging, config.logging.level.upper(), logging.INFO)
    
    # Configure structlog
    processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]
    
    if config.logging.format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    # Set up stdlib logging
    logging.basicConfig(
        format="%(message)s",
        level=level,
        stream=sys.stdout,
    )
    
    # File logging if configured
    if config.logging.file:
        file_handler = logging.FileHandler(config.logging.file)
        file_handler.setLevel(level)
        logging.getLogger().addHandler(file_handler)


class ResourceAgent:
    """
    Main resource agent class.
    
    Coordinates broker communication and job execution.
    Optionally acts as a campaign worker for distributed campaign management.
    """
    
    def __init__(self, config: Config):
        self.config = config
        self.logger = structlog.get_logger()
        
        # Initialize executor for tool/job execution
        work_dir = config.agent.get_work_dir()
        self.executor = JobExecutor(work_dir)
        
        # Initialize campaign processor if enabled
        self.campaign_processor = None
        if config.campaign_worker.enabled:
            from campaign_processor import CampaignProcessor
            # Note: send_message will be set by broker_client after connection
            self.campaign_processor = CampaignProcessor(
                config=config,
                send_message_func=None,  # Set by broker_client
            )
            self.logger.info(
                "Campaign worker mode enabled",
                max_campaigns=config.campaign_worker.max_campaigns,
                llm_provider=config.campaign_worker.llm_provider,
            )
        
        # Initialize broker client
        self.broker = BrokerClient(
            config=config,
            on_job=self._handle_job,
            on_cancel=self._handle_cancel,
            campaign_processor=self.campaign_processor,
        )
        
        self._shutdown_event = asyncio.Event()
    
    async def run(self):
        """Run the agent."""
        self.logger.info(
            "Starting resource agent",
            name=self.config.agent.get_name(),
            broker_url=self.config.broker.url
        )
        
        # Log capabilities at startup
        caps = detect_capabilities(
            storage_paths=self.config.capabilities.storage_paths or None,
            config=self.config.campaign_worker if self.config.campaign_worker.enabled else None,
        )
        self.logger.info(
            "Detected capabilities",
            cpu_cores=caps.cpu.cores_logical,
            memory_gb=f"{caps.memory.total_gb:.1f}",
            gpu_count=len(caps.gpus),
            gpus=[g.name for g in caps.gpus],
            ollama=caps.has_ollama,
        )
        
        # Run broker client
        await self.broker.run()
    
    async def shutdown(self):
        """Graceful shutdown."""
        self.logger.info("Shutting down...")
        await self.broker.stop()
        self._shutdown_event.set()
    
    async def _handle_job(self, job_data: dict) -> dict:
        """Handle assigned job."""
        job_id = job_data.get("job_id")
        tool_slug = job_data.get("tool_slug", "unknown")
        
        self.logger.info(
            "Executing job",
            job_id=job_id,
            tool_slug=tool_slug
        )
        
        # Execute the job
        result = await self.executor.execute(job_data)
        
        if result.get("success"):
            self.logger.info("Job completed", job_id=job_id)
        else:
            self.logger.error(
                "Job failed",
                job_id=job_id,
                error=result.get("error")
            )
        
        return result
    
    def _handle_cancel(self, job_id: str):
        """Handle job cancellation."""
        self.logger.info("Cancelling job", job_id=job_id)
        self.executor.cancel_job(job_id)


async def main():
    """Main entry point."""
    # Load configuration
    config = load_config()
    
    # Set up logging
    setup_logging(config)
    
    # Validate configuration
    if not config.broker.api_key:
        print("ERROR: broker.api_key not configured!")
        print("Either set BROKER_API_KEY environment variable or edit config.yaml")
        sys.exit(1)
    
    # Create and run agent
    agent = ResourceAgent(config)
    
    # Set up signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        asyncio.create_task(agent.shutdown())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            signal.signal(sig, lambda s, f: asyncio.create_task(agent.shutdown()))
    
    try:
        await agent.run()
    except KeyboardInterrupt:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
