"""P10-C2a: bounded PDF provenance and published public vector chunks."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from migrations.helpers import scoped_columns, scoped_table
from app.modules.knowledge.vector import Vector

revision = '20261106_1300_knowledge_embeddings'
down_revision = '20261106_1200_customer_care'
branch_labels = None
depends_on = None

CASES = "'intake','copilot','summary','reorder_note','briefing','procurement','campaign_draft','payment_followup','safety','grounding','dictation','customer_care'"


def upgrade() -> None:
    # smb_owner is not superuser. The prepared DB already has vector 0.8.7; on a fresh installation,
    # the DB administrator must preinstall this untrusted extension before running owner migrations.
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.drop_constraint('use_case', 'ai_runs', type_='check')
    op.create_check_constraint('use_case', 'ai_runs', f"use_case in ({CASES},'knowledge_index','knowledge_query')")
    for col in (
        sa.Column('source', sa.String(20), nullable=False, server_default='manual'),
        sa.Column('source_filename', sa.String(120)),
        sa.Column('source_page_start', sa.Integer), sa.Column('source_page_end', sa.Integer),
        sa.Column('search_index_state', sa.String(20), nullable=False, server_default='not_applicable'),
        sa.Column('search_index_error', sa.String(80)), sa.Column('search_index_model', sa.Text),
        sa.Column('index_generation', sa.Integer, nullable=False, server_default='0'),
    ):
        op.add_column('kb_articles', col)
    op.create_check_constraint('index_state', 'kb_articles',
        "search_index_state in ('not_applicable','pending','indexed','unavailable','failed')")
    op.create_check_constraint('source', 'kb_articles', "source in ('manual','pdf_import')")
    op.create_check_constraint('source_pages', 'kb_articles',
        '(source_page_start is null and source_page_end is null) or '
        '(source_page_start > 0 and source_page_end >= source_page_start and source_page_end <= 100)')
    op.execute("update kb_articles set search_index_state='pending' where status='published' and visibility='public'")
    op.create_table('kb_chunks', *scoped_columns(),
        sa.Column('article_id', UUID(as_uuid=True), nullable=False), sa.Column('version_no', sa.Integer, nullable=False),
        sa.Column('chunk_index', sa.Integer, nullable=False),
        sa.Column('start_offset', sa.Integer, nullable=False), sa.Column('end_offset', sa.Integer, nullable=False),
        sa.Column('text', sa.Text, nullable=False), sa.Column('token_estimate', sa.Integer, nullable=False),
        sa.Column('embedding', Vector(), nullable=False), sa.Column('model_id', sa.Text, nullable=False),
        sa.UniqueConstraint('business_id','id'),
        sa.UniqueConstraint('business_id','article_id','version_no','chunk_index'),
        sa.ForeignKeyConstraint(['business_id','article_id'], ['kb_articles.business_id','kb_articles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['business_id','article_id','version_no'],
            ['kb_article_versions.business_id','kb_article_versions.article_id','kb_article_versions.version_no'],ondelete='CASCADE'),
        sa.CheckConstraint('start_offset >= 0 and end_offset > start_offset and token_estimate > 0 and chunk_index >= 0', name='span'))
    op.create_index('ix_kb_chunks_cosine','kb_chunks',['embedding'],postgresql_using='hnsw',
                    postgresql_ops={'embedding':'vector_cosine_ops'})
    op.execute('grant select, insert, update, delete on kb_chunks to smb_app')
    scoped_table('kb_chunks')
    # Defense in depth: stale/private publications cannot accept chunks, even through direct application SQL.
    op.execute("""create function kb_chunk_publication_guard() returns trigger language plpgsql as $$
      begin
        if not exists (select 1 from kb_articles a join kb_article_versions v
          on v.business_id=a.business_id and v.article_id=a.id and v.version_no=a.published_version
          where a.business_id=NEW.business_id and a.id=NEW.article_id and a.status='published'
          and a.visibility='public' and a.published_version=NEW.version_no and v.published_at is not null
          and NEW.end_offset <= length(v.body_markdown)
          and NEW.text=substring(v.body_markdown from NEW.start_offset+1 for NEW.end_offset-NEW.start_offset))
        then raise exception 'invalid knowledge chunk publication'; end if;
        return NEW;
      end $$""")
    op.execute('create trigger kb_chunks_publication before insert or update on kb_chunks '
               'for each row execute function kb_chunk_publication_guard()')
    op.execute("""create function kb_invalidate_chunks() returns trigger language plpgsql as $$
      begin
        if NEW.status <> 'published' or NEW.visibility <> 'public' or
           NEW.published_version is distinct from OLD.published_version then
          delete from kb_chunks where business_id=NEW.business_id and article_id=NEW.id;
        end if;
        return NEW;
      end $$""")
    op.execute('create trigger kb_articles_invalidate after update of status,visibility,published_version on kb_articles '
               'for each row execute function kb_invalidate_chunks()')


def downgrade() -> None:
    op.execute('drop trigger kb_articles_invalidate on kb_articles')
    op.execute('drop function kb_invalidate_chunks()')
    op.drop_table('kb_chunks')
    op.execute('drop function kb_chunk_publication_guard()')
    for name in ('index_state','source','source_pages'):
        op.drop_constraint(name,'kb_articles',type_='check')
    for name in ('source','source_filename','source_page_start','source_page_end','search_index_state',
                 'search_index_error','search_index_model','index_generation'):
        op.drop_column('kb_articles',name)
    op.execute("delete from ai_run_steps where run_id in (select id from ai_runs where use_case in ('knowledge_index','knowledge_query'))")
    op.execute("delete from ai_runs where use_case in ('knowledge_index','knowledge_query')")
    op.drop_constraint('use_case','ai_runs',type_='check')
    op.create_check_constraint('use_case','ai_runs',f'use_case in ({CASES})')
    # Preserve the administrator-installed extension; it has no tables and may have other consumers.
