"""Agent framework for Money Agents."""
from app.agents.base import BaseAgent, AgentContext, AgentResult
from app.agents.proposal_writer import ProposalWriterAgent, proposal_writer_agent
from app.agents.opportunity_scout import OpportunityScoutAgent
from app.agents.tool_scout import ToolScoutAgent
from app.agents.campaign_manager import CampaignManagerAgent, campaign_manager_agent
from app.agents.campaign_discussion import CampaignDiscussionAgent, campaign_discussion_agent
from app.agents.spend_advisor import SpendAdvisorAgent, spend_advisor_agent

# Singleton instance for dependency injection
opportunity_scout_agent = OpportunityScoutAgent()
tool_scout_agent = ToolScoutAgent()

__all__ = [
    "BaseAgent",
    "AgentContext",
    "AgentResult",
    "ProposalWriterAgent",
    "proposal_writer_agent",
    "OpportunityScoutAgent",
    "opportunity_scout_agent",
    "ToolScoutAgent",
    "tool_scout_agent",
    "CampaignManagerAgent",
    "campaign_manager_agent",
    "CampaignDiscussionAgent",
    "campaign_discussion_agent",
    "SpendAdvisorAgent",
    "spend_advisor_agent",
]
