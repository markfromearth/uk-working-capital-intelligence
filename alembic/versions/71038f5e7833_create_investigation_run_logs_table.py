"""create_investigation_run_logs_table

Revision ID: 71038f5e7833
Revises: 32c8c59095d6
Create Date: 2026-10-21 11:07:07.522158

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '71038f5e7833'
down_revision: Union[str, Sequence[str], None] = '32c8c59095d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'investigation_run_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('investigation_id', sa.Integer(), nullable=True),
        sa.Column('crn', sa.String(length=10), nullable=False),
        sa.Column('tool_name', sa.String(length=100), nullable=False),
        sa.Column('missingness_pattern', sa.String(length=100), nullable=True),
        sa.Column('prior_action', sa.String(length=100), nullable=True),
        sa.Column('posterior_action', sa.String(length=100), nullable=True),
        sa.Column('prior_tier', sa.String(length=50), nullable=True),
        sa.Column('posterior_tier', sa.String(length=50), nullable=True),
        sa.Column('prior_uncertainty', sa.Float(), nullable=False, server_default='0.5'),
        sa.Column('posterior_uncertainty', sa.Float(), nullable=False, server_default='0.5'),
        sa.Column('action_changed', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('tool_success', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['crn'], ['company_registry.crn'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['investigation_id'], ['investigation_decisions.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_investigation_run_logs_crn'), 'investigation_run_logs', ['crn'], unique=False)
    op.create_index(op.f('ix_investigation_run_logs_investigation_id'), 'investigation_run_logs', ['investigation_id'], unique=False)
    op.create_index(op.f('ix_investigation_run_logs_tool_name'), 'investigation_run_logs', ['tool_name'], unique=False)
    op.create_index(op.f('ix_investigation_run_logs_missingness_pattern'), 'investigation_run_logs', ['missingness_pattern'], unique=False)
    op.create_index(op.f('ix_investigation_run_logs_created_at'), 'investigation_run_logs', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_investigation_run_logs_created_at'), table_name='investigation_run_logs')
    op.drop_index(op.f('ix_investigation_run_logs_missingness_pattern'), table_name='investigation_run_logs')
    op.drop_index(op.f('ix_investigation_run_logs_tool_name'), table_name='investigation_run_logs')
    op.drop_index(op.f('ix_investigation_run_logs_investigation_id'), table_name='investigation_run_logs')
    op.drop_index(op.f('ix_investigation_run_logs_crn'), table_name='investigation_run_logs')
    op.drop_table('investigation_run_logs')
