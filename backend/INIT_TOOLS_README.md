# Tools Catalog Initialization

## Overview

The `init_tools_catalog.py` script automatically populates the Money Agents tools catalog based on available API keys in your `.env` file.

## Features

- ✅ **Idempotent**: Safe to run multiple times
- ✅ **Smart Detection**: Checks which API keys are configured
- ✅ **No Duplicates**: Won't create duplicate tools
- ✅ **Graceful Handling**: Disables tools when API keys removed
- ✅ **Professional Content**: Comprehensive tool descriptions for agents

## Prerequisites

### Required API Keys

At least ONE of these LLM providers must be configured:

1. **Z.ai** (Preferred - free tier)
   ```bash
   Z_AI_API_KEY=your_api_key_here
   ```

2. **Anthropic Claude** (Fallback - high reliability)
   ```bash
   ANTHROPIC_API_KEY=your_api_key_here
   ```

3. **OpenAI** (Tertiary - final fallback)
   ```bash
   OPENAI_API_KEY=your_api_key_here
   ```

### Strongly Recommended

**Serper Web Search** - Required for Opportunity Scout agent to function:
```bash
SERPER_API_KEY=your_api_key_here
```

### Optional API Keys

**ElevenLabs** - Voice generation for content creation:
```bash
ELEVENLABS_API_KEY=your_api_key_here
```

**Suno** - Music generation (manual workflow):
```bash
USE_SUNO=true
```

## Usage

1. **Configure your `.env` file** with API keys:
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

2. **Run the initialization script**:
   ```bash
   cd backend
   python init_tools_catalog.py
   ```

3. **Check the output** for created tools:
   ```
   🛠️  Money Agents - Tools Catalog Initialization
   ======================================================================
   
   📋 Checking system user...
      System user: system@money-agents.dev
   
   🔑 Checking API keys...
      Z.ai API Key: ✅ Configured
      Anthropic API Key: ✅ Configured
      OpenAI API Key: ❌ Missing
      Serper API Key: ✅ Configured
      ElevenLabs API Key: ✅ Configured
      Suno Enabled: ❌ No
   
   🔨 Processing tools...
   
     ✅ Created: Z.ai GLM-4.7
     ✅ Created: Anthropic Claude Sonnet 4.5
     ⏭️  Skipped (API key not configured): OpenAI GPT-5.2
     ✅ Created: OpenAI DALL-E 3
     ✅ Created: Serper Web Search
     ✅ Created: ElevenLabs Voice Generation
     ⏭️  Skipped (API key not configured): Suno AI Music Generation
   
   ======================================================================
   ✅ Tools catalog initialization complete!
   ======================================================================
   ```

## Re-running the Script

The script is idempotent and can be safely re-run:

### Scenarios

1. **Adding a new API key**:
   - Add the key to `.env`
   - Re-run the script
   - New tool will be created

2. **Removing an API key**:
   - Remove the key from `.env`
   - Re-run the script
   - Tool status changes to `DEPRECATED` (not deleted)

3. **Updating tool definitions**:
   - Edit `TOOL_DEFINITIONS` in the script
   - Re-run the script
   - Existing tools will be updated with new information

4. **No changes needed**:
   - Re-run the script
   - Output: `ℹ️  Already exists: [Tool Name]`

## Tools Created

The script creates the following tools (when API keys available):

### LLM Tools (3-Tier Fallback)

1. **Z.ai GLM-4.7** (Primary)
   - Cost-effective, free tier
   - Fast inference
   - First choice for all operations

2. **Anthropic Claude Sonnet 4.5** (Fallback)
   - High reliability
   - Complex reasoning
   - Used when primary fails

3. **OpenAI GPT-5.2** (Tertiary)
   - Enterprise-grade
   - Final fallback
   - Highest cost

### Content Creation Tools

4. **OpenAI DALL-E 3**
   - AI image generation
   - Marketing visuals
   - Product mockups

5. **ElevenLabs Voice**
   - Text-to-speech
   - Voiceovers for videos
   - Podcast narration

6. **Suno AI Music** (Manual Workflow)
   - Music generation
   - Background tracks
   - Requires human interaction

### Research Tool

7. **Serper Web Search** (Critical)
   - Real-time Google search
   - Market research
   - Competition analysis
   - **Required for Opportunity Scout agent**

## Tool Status

Tools are created with `status=IMPLEMENTED`, meaning they are ready to use.

## Validation

The script validates your configuration:

### ✅ Valid Configuration
- At least one LLM API key configured
- Serper API key configured (recommended)

### ❌ Invalid Configuration
```
❌ ERROR: No LLM API key configured!
   At least one of the following is required:
   - Z_AI_API_KEY (preferred)
   - ANTHROPIC_API_KEY
   - OPENAI_API_KEY
```

### ⚠️ Warning (but will proceed)
```
⚠️  WARNING: SERPER_API_KEY not configured!
   Web search is required for Opportunity Scout agent.
   System will have limited functionality.
```

## Viewing Tools

After running the script:

1. **Via Frontend**: Navigate to http://localhost:5173/tools
2. **Via API**: GET http://localhost:8000/api/v1/tools
3. **Via Database**: Check the `tools` table

## Troubleshooting

### Script fails with "No module named 'app'"

**Solution**: Run from the `backend` directory:
```bash
cd backend
python init_tools_catalog.py
```

### "No LLM API key configured" error

**Solution**: Add at least one LLM API key to `.env`:
```bash
# Preferred (free tier)
Z_AI_API_KEY=your_z_ai_api_key_here

# OR

# High reliability
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# OR

# Enterprise
OPENAI_API_KEY=your_openai_api_key_here
```

### Database connection error

**Solution**: Ensure PostgreSQL is running:
```bash
docker compose up -d money-agents-postgres
```

### Tools not showing in frontend

**Solution**: 
1. Verify tools were created: Check script output
2. Refresh browser cache
3. Check API endpoint: `curl http://localhost:8000/api/v1/tools`

## Next Steps

After initializing the tools catalog:

1. ✅ Tools are now available in the catalog
2. ⏭️ Implement LLM service with fallback logic
3. ⏭️ Create agent base class with tool discovery
4. ⏭️ Build first agent (Proposal Writer)
5. ⏭️ Add file attachments for Suno workflow

## Technical Details

### System User

The script creates a system user (`system@money-agents.dev`) that owns all initialized tools. This user:
- Cannot log in (password is hashed dummy value)
- Has admin role and superuser privileges
- Is used as both requester and approver for system tools

### Tool Fields Populated

Each tool includes:
- Name, slug, category, description
- Usage instructions with code examples
- Required environment variables
- Integration complexity
- Cost model with pricing details
- Strengths, weaknesses, best use cases
- External documentation links
- Version and priority information

## Related Documentation

- [.env.example](../.env.example) - Environment configuration template
