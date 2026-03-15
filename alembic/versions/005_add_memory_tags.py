revision = '005'
down_revision = '004'

from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table('memory_tags',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('memory_id', sa.Integer(), nullable=False),
        sa.Column('tag', sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(['memory_id'], ['memory.id']),
        sa.PrimaryKeyConstraint('id')
    )

def downgrade():
    op.drop_table('memory_tags')

