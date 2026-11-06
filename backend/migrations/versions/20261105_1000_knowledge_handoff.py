"""P10-C1 part 1: versioned knowledge and enforced AI hand-over."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID, TSVECTOR

from migrations.helpers import composite_fk, scoped_columns, scoped_table

revision = '20261105_1000_knowledge_handoff'
down_revision = '20261105_0900_chat_delivery'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('ai_mode', sa.String(10), nullable=False, server_default='auto'))
    op.add_column('conversations', sa.Column('ai_generation', sa.Integer, nullable=False, server_default='0'))
    op.add_column('conversations', sa.Column('ai_paused_at', sa.DateTime(timezone=True)))
    op.add_column('messages', sa.Column('ai_generation', sa.Integer, nullable=False, server_default='0'))
    op.create_check_constraint('ai_ownership', 'conversations',
        "ai_generation >= 0 and ((ai_mode='auto' and ai_paused_at is null) or (ai_mode='human' and ai_paused_at is not null))")
    op.execute("update conversations set ai_mode='human', ai_paused_at=handoff_requested_at, ai_generation=1 "
               "where handoff_requested_at is not null")
    op.create_table('kb_articles', *scoped_columns(),
        sa.Column('title', sa.String(200), nullable=False), sa.Column('slug', sa.String(100), nullable=False),
        sa.Column('visibility', sa.String(10), nullable=False, server_default='public'),
        sa.Column('status', sa.String(12), nullable=False, server_default='draft'),
        sa.Column('current_version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('published_version', sa.Integer), sa.Column('created_by', UUID(as_uuid=True)),
        sa.UniqueConstraint('business_id','id'), sa.UniqueConstraint('business_id','slug'),
        sa.CheckConstraint("visibility in ('public','internal')",name='visibility'),
        sa.CheckConstraint("status in ('draft','published','archived')",name='status'),
        sa.CheckConstraint('current_version > 0 and (published_version is null or '
            '(published_version > 0 and published_version <= current_version))', name='revision'),
        sa.CheckConstraint("status != 'published' or published_version is not null", name='publication'))
    op.create_table('kb_article_versions', *scoped_columns(),
        sa.Column('article_id', UUID(as_uuid=True),nullable=False),
        sa.Column('version_no',sa.Integer,nullable=False), sa.Column('title',sa.String(200),nullable=False),
        sa.Column('body_markdown',sa.Text,nullable=False),
        sa.Column('tags',ARRAY(sa.String(50)),nullable=False,server_default='{}'),
        sa.Column('author',UUID(as_uuid=True)),sa.Column('published_at',sa.DateTime(timezone=True)),
        sa.Column('search_vector',TSVECTOR,sa.Computed(
            "setweight(to_tsvector('simple', title), 'A') || to_tsvector('simple', body_markdown)",persisted=True)),
        composite_fk('article_id','kb_articles'),sa.UniqueConstraint('business_id','id'),
        sa.UniqueConstraint('business_id','article_id','version_no'),
        sa.CheckConstraint('version_no > 0 and length(body_markdown) <= 20000',name='body'))
    op.create_index('ix_kb_published_search','kb_article_versions',['search_vector'],
                    postgresql_using='gin',postgresql_where=sa.text('published_at is not null'))
    op.create_table('kb_suggestions', *scoped_columns(),
        sa.Column('conversation_id',UUID(as_uuid=True),nullable=False),
        sa.Column('question_text',sa.Text,nullable=False),
        sa.Column('status',sa.String(12),nullable=False,server_default='open'),
        sa.Column('article_id',UUID(as_uuid=True)),sa.UniqueConstraint('business_id','id'),
        composite_fk('conversation_id','conversations'),composite_fk('article_id','kb_articles'),
        sa.CheckConstraint("status in ('open','done','dismissed')",name='status'),
        sa.CheckConstraint('length(question_text) <= 4000',name='question'))
    op.create_index('ix_kb_suggestions_open','kb_suggestions',
        ['business_id','conversation_id',sa.text('md5(question_text)')],unique=True,
        postgresql_where=sa.text("status='open'"))
    for table in ('kb_articles','kb_article_versions','kb_suggestions'):
        op.execute(f'grant select, insert, update, delete on {table} to smb_app')
        scoped_table(table,append=table=='kb_article_versions')


def downgrade() -> None:
    for table in ('kb_suggestions','kb_article_versions','kb_articles'):
        op.drop_table(table)
    op.drop_constraint('ai_ownership','conversations',type_='check')
    op.drop_column('messages','ai_generation')
    for column in ('ai_paused_at','ai_generation','ai_mode'):
        op.drop_column('conversations',column)
