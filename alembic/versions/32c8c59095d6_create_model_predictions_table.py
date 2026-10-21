"""create model_predictions table

Revision ID: 32c8c59095d6
Revises: 
Create Date: 2026-10-21 11:01:40.012115

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '32c8c59095d6'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'model_predictions',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('crn', sa.String(length=10), sa.ForeignKey('company_registry.crn', ondelete='CASCADE'), nullable=False),
        sa.Column('as_of', sa.DateTime(), nullable=False),
        sa.Column('model_version', sa.String(length=50), nullable=False),
        sa.Column('p_value', sa.Float(), nullable=False),
        sa.Column('rules_p_value', sa.Float(), nullable=False),
        sa.Column('feature_vector_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_model_predictions_crn', 'model_predictions', ['crn'])
    op.create_index('ix_model_predictions_as_of', 'model_predictions', ['as_of'])
    op.create_index('ix_model_predictions_model_version', 'model_predictions', ['model_version'])
    op.create_index('ix_model_predictions_created_at', 'model_predictions', ['created_at'])
    op.create_index('ix_model_predictions_crn_as_of', 'model_predictions', ['crn', 'as_of'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_model_predictions_crn_as_of', table_name='model_predictions')
    op.drop_index('ix_model_predictions_created_at', table_name='model_predictions')
    op.drop_index('ix_model_predictions_model_version', table_name='model_predictions')
    op.drop_index('ix_model_predictions_as_of', table_name='model_predictions')
    op.drop_index('ix_model_predictions_crn', table_name='model_predictions')
    op.drop_table('model_predictions')
