"""Initial users and privacy-preserving chat metadata schema."""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('users',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('username', sa.String(128), nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('admin','user','readonly')", name='valid_user_role'))
    op.create_index('ix_users_username', 'users', ['username'], unique=True)
    op.create_table('chat_records',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('request_id', sa.String(36), nullable=False, unique=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('model', sa.String(256), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False),
        sa.Column('completion_tokens', sa.Integer(), nullable=False),
        sa.Column('total_tokens', sa.Integer(), nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=False),
        sa.Column('cached', sa.Boolean(), nullable=False),
        sa.Column('question', sa.Text(), nullable=True),
        sa.Column('answer', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_chat_records_user_id', 'chat_records', ['user_id'])
    op.create_index('ix_chat_records_created_at', 'chat_records', ['created_at'])


def downgrade():
    op.drop_index('ix_chat_records_created_at', table_name='chat_records')
    op.drop_index('ix_chat_records_user_id', table_name='chat_records')
    op.drop_table('chat_records')
    op.drop_index('ix_users_username', table_name='users')
    op.drop_table('users')
