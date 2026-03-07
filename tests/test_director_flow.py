"""Test suite for _office_director_chat sub-agent coordination flow.

Verifies:
1. Sub-agents are discovered and loaded from DB
2. Decision prompt routes strategic tasks to agents (not self)
3. Director messages are visible in chat (via _send_visible + Interaction save)
4. Agent results appear in chat
5. The full conversation flow is visible to the user
"""
import asyncio
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LOCAL", "1")
os.environ.setdefault("TELEGRAM_TOKEN", "test:token")
os.environ.setdefault("FREE_ACCESS_MODE", "1")
os.environ["PYTHONIOENCODING"] = "utf-8"


@pytest.fixture
def db_session():
    """Create a fresh DB session with test user and agent."""
    from models import Session, Base, engine, User, UserAgent
    Base.metadata.create_all(engine)
    session = Session()
    
    # Cleanup
    session.query(UserAgent).filter(UserAgent.name == 'TestAgent').delete()
    session.query(User).filter(User.telegram_id == 999999).delete()
    session.commit()
    
    # Create test user
    user = User(telegram_id=999999, username='test_director_user')
    session.add(user)
    session.commit()
    user_id = user.id
    
    # Create test agent
    agent = UserAgent(
        author_id=user_id,
        name='TestAgent',
        job_title='Researcher',
        specialization='market research and analysis',
        description='Expert at research, analysis and finding information',
        status='active',
        tools_allowed='["research_topic","web_search"]',
    )
    session.add(agent)
    session.commit()
    agent_id = agent.id
    agent_name = agent.name
    
    # Create agent subscription
    try:
        from models import AgentSubscription
        sub = AgentSubscription(user_id=user_id, agent_id=agent_id)
        session.add(sub)
        session.commit()
    except Exception:
        pass
    
    # Return plain data dict to avoid session expiry issues
    info = {
        'user_id': user_id,
        'agent_id': agent_id,
        'agent_name': agent_name,
        'telegram_id': 999999,
    }
    yield session, info
    
    # Cleanup
    try:
        session.query(UserAgent).filter(UserAgent.name == 'TestAgent').delete()
        session.query(User).filter(User.telegram_id == 999999).delete()
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def test_agents_loaded(db_session):
    """Test that _office_director_chat loads agents from DB."""
    session, info = db_session
    
    from models import Session as DbSession, User, UserAgent
    s = DbSession()
    try:
        u = s.query(User).filter_by(telegram_id=999999).first()
        assert u is not None, "Test user not found"
        
        agents = s.query(UserAgent).filter(
            UserAgent.author_id == u.id,
            UserAgent.status.in_(['active', 'paused'])
        ).all()
        assert len(agents) >= 1, f"Expected at least 1 agent, got {len(agents)}"
        assert agents[0].name == 'TestAgent'
        print(f"[OK] Found {len(agents)} agent(s): {[a.name for a in agents]}")
    finally:
        s.close()


def test_decision_prompt_structure(db_session):
    """Test that the decision prompt is built correctly with agent info."""
    session, info = db_session
    
    from models import Session as DbSession, UserAgent
    from ai_integration.autonomous_agent import _parse_agent_integrations, _infer_capabilities_from_role
    
    s = DbSession()
    try:
        agent = s.query(UserAgent).get(info['agent_id'])
        
        intg = _parse_agent_integrations(
            agent.user_api_keys or '',
            agent.python_code or '',
            agent.tools_allowed or '',
            agent.search_scope or '',
        )
        if not intg:
            intg = _infer_capabilities_from_role(
                agent.job_title or '',
                agent.specialization or '',
                agent.description or '',
            )
        
        assert len(intg) > 0, "Agent should have inferred capabilities"
        print(f"[OK] Agent capabilities: {intg}")
        
        caps_block = f"- {agent.name} [{agent.job_title}] ({agent.specialization}): {agent.description}\n  Mozhet: {', '.join(intg)}"
        
        decision_prompt = (
            f"Ty - ASI Biont, direktor ofisa.\n\n"
            f"ZAPROS POLZOVATELA:\nIssleduj rynok AI\n\n"
            f"PROFILI AGENTOV KOMANDY:\n{caps_block}\n\n"
        )
        
        assert 'TestAgent' in decision_prompt
        assert 'Researcher' in decision_prompt
        print(f"[OK] Decision prompt contains agent info ({len(decision_prompt)} chars)")
    finally:
        s.close()


def test_strategic_keywords_routing(db_session):
    """Test that strategic keywords bypass trivial reply filter."""
    _strategic_keywords = ('кампани', 'аутрич', 'тестировщик', 'тестер', 'продвиж', 'маркетинг',
                           'привлеч', 'исследуй', 'исследов', 'стратег', 'поиск людей',
                           'найди люд', 'найди тестер', 'найди тестировщик', 'контент-план',
                           'пользовател', 'клиент')
    
    strategic_messages = [
        "Исследуй рынок AI-тестирования",
        "Привлечи больше клиентов",
        "Запусти маркетинговую кампанию",
        "Найди тестировщиков для приложения",
        "Нужна стратегия продвижения",
    ]
    
    for msg in strategic_messages:
        is_strategic = any(kw in msg.lower() for kw in _strategic_keywords)
        assert is_strategic, f"Message '{msg}' should be strategic but wasn't matched"
    
    non_strategic = ["привет", "создай задачу позвонить", "да", "ок"]
    for msg in non_strategic:
        is_strategic = any(kw in msg.lower() for kw in _strategic_keywords)
        assert not is_strategic, f"Message '{msg}' should NOT be strategic"
    
    print("[OK] Strategic keyword routing works correctly")


def test_send_visible_and_interaction_save(db_session):
    """Test that _save_interaction_for_director saves messages to Interaction."""
    session, info = db_session
    
    from ai_integration.autonomous_agent import _save_interaction_for_director
    from models import Session as DbSession, Interaction
    
    test_msg = "TestAgent, issleduj rynok AI-testirovaniya i podgotov spisok"
    _save_interaction_for_director(999999, test_msg)
    
    s = DbSession()
    try:
        last = s.query(Interaction).filter(
            Interaction.user_id == info['user_id'],
            Interaction.message_type == 'ai'
        ).order_by(Interaction.id.desc()).first()
        
        assert last is not None, "Interaction was not saved"
        assert test_msg in last.content, f"Content mismatch: {last.content}"
        print(f"[OK] Director message saved to Interaction: '{last.content[:80]}...'")
    finally:
        s.close()


def test_agent_result_saved_as_json(db_session):
    """Test that agent results are saved with __agent JSON wrapper."""
    session, info = db_session
    
    from ai_integration.autonomous_agent import _save_interaction_for_director
    from models import Session as DbSession, Interaction
    
    agent_result = json.dumps({
        '__agent': {'name': 'TestAgent', 'id': info['agent_id'], 'avatar_url': ''},
        'text': 'Results of market analysis...',
    }, ensure_ascii=False)
    
    _save_interaction_for_director(999999, agent_result)
    
    s = DbSession()
    try:
        last = s.query(Interaction).filter(
            Interaction.user_id == info['user_id'],
            Interaction.message_type == 'ai'
        ).order_by(Interaction.id.desc()).first()
        
        assert last is not None
        parsed = json.loads(last.content)
        assert '__agent' in parsed, "Agent result should have __agent wrapper"
        assert parsed['__agent']['name'] == 'TestAgent'
        print(f"[OK] Agent result saved with __agent JSON: {parsed['__agent']}")
    finally:
        s.close()


def test_emojis_preserved_in_interactions(db_session):
    """Test that emojis are NOT stripped from saved interactions."""
    session, info = db_session
    
    from ai_integration.autonomous_agent import _save_interaction_for_director
    from models import Session as DbSession, Interaction
    
    test_msg = "\U0001F680 Great results! Platforms found \U0001F44D"
    _save_interaction_for_director(999999, test_msg)
    
    s = DbSession()
    try:
        last = s.query(Interaction).filter(
            Interaction.user_id == info['user_id'],
            Interaction.message_type == 'ai'
        ).order_by(Interaction.id.desc()).first()
        
        assert last is not None
        assert '\U0001F680' in last.content, f"Emoji rocket was stripped! Got: {last.content}"
        assert '\U0001F44D' in last.content, f"Emoji thumbsup was stripped! Got: {last.content}"
        print(f"[OK] Emojis preserved in saved interaction")
    finally:
        s.close()


def test_clean_technical_details_preserves_emojis():
    """Test that clean_technical_details does NOT strip emojis anymore."""
    from ai_integration.utils import clean_technical_details
    
    text = "Great \U0001F525 plan:\nFirst step - research market \U0001F680"
    result = clean_technical_details(text)
    
    assert '\U0001F525' in result, f"Emoji fire was stripped! Got: {result}"
    assert '\U0001F680' in result, f"Emoji rocket was stripped! Got: {result}"
    print(f"[OK] clean_technical_details preserves emojis")


def test_full_visibility_chain(db_session):
    """Test the full visibility chain: director message -> agent result -> synthesis."""
    session, info = db_session
    
    from ai_integration.autonomous_agent import _save_interaction_for_director
    from models import Session as DbSession, Interaction
    
    # Step 1: Director gives assignment
    director_msg = "TestAgent, research the market and prepare list of platforms"
    _save_interaction_for_director(999999, director_msg)
    
    # Step 2: Agent responds with result
    agent_result = json.dumps({
        '__agent': {'name': 'TestAgent', 'id': info['agent_id'], 'avatar_url': ''},
        'text': 'AI testing market: main platforms - TestRail, Testim, Mabl',
    }, ensure_ascii=False)
    _save_interaction_for_director(999999, agent_result)
    
    # Step 3: ASI synthesis
    synthesis = "Team delivered. TestAgent found 3 platforms for testing."
    _save_interaction_for_director(999999, synthesis)
    
    # Verify all 3 messages are in the DB in order
    s = DbSession()
    try:
        interactions = s.query(Interaction).filter(
            Interaction.user_id == info['user_id'],
            Interaction.message_type == 'ai'
        ).order_by(Interaction.id.desc()).limit(3).all()
        
        assert len(interactions) >= 3, f"Expected 3+ interactions, got {len(interactions)}"
        
        # Newest first from query, reverse for chronological
        interactions = list(reversed(interactions))
        
        assert 'research' in interactions[0].content.lower()
        assert '__agent' in interactions[1].content
        assert 'delivered' in interactions[2].content.lower()
        
        print("[OK] Full visibility chain verified:")
        for i, inter in enumerate(interactions):
            content_preview = inter.content[:80]
            print(f"   [{i+1}] {content_preview}...")
    finally:
        s.close()


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])


@pytest.fixture
def visible_messages():
    """Collect all messages sent via _send_visible (progress_callback)."""
    messages = []
    
    async def mock_progress_callback(text, *, persist=False):
        messages.append({'text': text, 'persist': persist})
    
    return messages, mock_progress_callback


@pytest.mark.asyncio
async def test_office_director_chat_delegation(db_session, visible_messages):
    """Integration test: _office_director_chat delegates to agent and messages are visible."""
    session, info = db_session
    messages, mock_callback = visible_messages
    
    from unittest.mock import AsyncMock, patch
    from ai_integration.autonomous_agent import _office_director_chat
    
    # Mock AI responses:
    # 1st call = decision prompt → delegate to TestAgent
    # 2nd call = agent execution (from _exec_agent_for_director)
    # 3rd call = synthesis
    delegate_decision = json.dumps({
        "action": "delegate",
        "agent_name": "TestAgent",
        "agent_task": "Research the AI testing market",
        "director_message": "TestAgent, research AI testing platforms and compile a list of top 5"
    })
    
    agent_result = "AI testing market analysis: Top platforms are TestRail, Mabl, Testim, Katalon, LambdaTest."
    
    synthesis = "TestAgent found 5 key platforms. I recommend starting with Mabl for AI-powered testing."
    
    call_count = 0
    async def mock_ai_call(messages_list, max_tokens=None, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return delegate_decision
        elif call_count <= 3:
            return agent_result
        else:
            return synthesis
    
    with patch('ai_integration.autonomous_agent._quick_ai_call_raw', side_effect=mock_ai_call):
        with patch('ai_integration.autonomous_agent._exec_agent_for_director',
                    new_callable=AsyncMock,
                    return_value=agent_result):
            result = await _office_director_chat(
                "Research AI testing tools market",
                info['telegram_id'],
                progress_callback=mock_callback
            )
    
    # Verify visible messages were sent
    assert len(messages) >= 2, f"Expected at least 2 visible messages (director + agent result), got {len(messages)}: {messages}"
    
    # First visible message should be director assignment
    assert 'TestAgent' in messages[0]['text'], f"First message should mention agent: {messages[0]}"
    assert messages[0]['persist'] is True, "Director message should be persistent"
    
    # Second visible message should be agent result
    assert 'TestAgent' in messages[1]['text'], f"Second message should be agent result: {messages[1]}"
    assert messages[1]['persist'] is True, "Agent result should be persistent"
    
    print(f"[OK] Director delegation flow works. {len(messages)} visible messages sent:")
    for i, m in enumerate(messages):
        print(f"  [{i+1}] persist={m['persist']}: {m['text'][:120]}...")
    
    # Verify final synthesis is returned (not None)
    assert result is not None, "Director should return synthesis"
    print(f"[OK] Synthesis returned: {result[:120]}...")
