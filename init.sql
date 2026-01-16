-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Channels table
CREATE TABLE IF NOT EXISTS channels (
    id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(255),
    channel_type VARCHAR(20) NOT NULL,  -- public_channel, private_channel, mpim, im
    is_archived BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

-- Users cache for resolving user IDs to names
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(255),
    real_name VARCHAR(255),
    display_name VARCHAR(255),
    is_bot BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

-- Messages table
CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    channel_id VARCHAR(20) NOT NULL REFERENCES channels(id),
    ts VARCHAR(20) NOT NULL,  -- Slack timestamp (unique per channel)
    user_id VARCHAR(20),
    text TEXT,
    thread_ts VARCHAR(20),  -- Parent message ts if this is a reply
    reply_count INTEGER DEFAULT 0,
    is_edited BOOLEAN DEFAULT FALSE,
    message_type VARCHAR(50) DEFAULT 'message',
    created_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}',
    UNIQUE(channel_id, ts)
);

-- Reactions table
CREATE TABLE IF NOT EXISTS reactions (
    id SERIAL PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,  -- Reaction emoji name
    user_id VARCHAR(20) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(message_id, name, user_id)
);

-- Message embeddings for vector search
CREATE TABLE IF NOT EXISTS message_embeddings (
    id SERIAL PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE UNIQUE,
    embedding vector(1536),  -- OpenAI ada-002 dimension
    model VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Sync state to track polling cursors
CREATE TABLE IF NOT EXISTS sync_state (
    channel_id VARCHAR(20) PRIMARY KEY REFERENCES channels(id),
    last_ts VARCHAR(20),  -- Last synced message timestamp
    last_sync_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Reminders (from Slack's deprecated API)
CREATE TABLE IF NOT EXISTS reminders (
    id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(20) NOT NULL,
    text TEXT,
    time TIMESTAMP WITH TIME ZONE,
    complete_ts TIMESTAMP WITH TIME ZONE,
    recurring BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_messages_channel_ts ON messages(channel_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(channel_id, thread_ts) WHERE thread_ts IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_reactions_message ON reactions(message_id);
CREATE INDEX IF NOT EXISTS idx_reactions_user ON reactions(user_id);

-- Vector similarity search index (using IVFFlat for better performance on large datasets)
-- Note: This index should be created after initial data load for best performance
-- CREATE INDEX IF NOT EXISTS idx_embeddings_vector ON message_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
