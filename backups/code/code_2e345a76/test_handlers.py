"""
Unit tests for handlers.py
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, User as TelegramUser, Chat
from handlers import router, start_handler, transcribe_audio_sync, transcribe_audio
import os
import tempfile


@pytest.fixture
def mock_message():
    """Create mock message for testing"""
    message = MagicMock(spec=Message)
    message.from_user = MagicMock(spec=TelegramUser)
    message.from_user.id = 123456789
    message.from_user.username = "testuser"
    message.from_user.first_name = "Test User"
    message.chat = MagicMock(spec=Chat)
    message.chat.id = 123456789
    message.bot = MagicMock()
    message.bot.send_message = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_start_handler_new_user(mock_message):
    """Test start handler creates new user"""
    with patch('handlers.Session') as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        # Mock that user doesn't exist
        mock_session.query.return_value.filter_by.return_value.first.return_value = None

        # Mock User creation
        with patch('handlers.User') as mock_user_class:
            mock_user = MagicMock()
            mock_user_class.return_value = mock_user

            await start_handler(mock_message)

            # Verify user was created
            mock_user_class.assert_called_once_with(
                telegram_id=123456789,
                username="testuser"
            )
            mock_session.add.assert_called_once_with(mock_user)
            mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_start_handler_existing_user(mock_message):
    """Test start handler with existing user"""
    with patch('handlers.Session') as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        # Mock that user exists
        mock_user = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = mock_user

        await start_handler(mock_message)

        # Verify no new user was created
        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_called()


def test_transcribe_audio_sync_no_file():
    """Test transcribe_audio_sync with non-existent file"""
    result = transcribe_audio_sync("/non/existent/file.ogg")
    assert result is None


def test_transcribe_audio_sync_voice_recognition_unavailable():
    """Test transcribe_audio_sync when voice recognition is unavailable"""
    with patch('handlers.VOICE_RECOGNITION_AVAILABLE', False):
        result = transcribe_audio_sync("/some/file.ogg")
        assert result is None


def test_transcribe_audio_sync_success():
    """Test successful audio transcription"""
    # Create temporary audio file for testing
    with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_file:
        temp_path = temp_file.name
        # Write some dummy data
        temp_file.write(b'dummy audio data')

    try:
        with patch('handlers.VOICE_RECOGNITION_AVAILABLE', True):
            with patch('handlers.AudioSegment.from_ogg') as mock_audio:
                mock_audio_instance = MagicMock()
                mock_audio.return_value = mock_audio_instance
                mock_audio_instance.export = MagicMock()

                with patch('handlers.sr.Recognizer') as mock_recognizer_class:
                    mock_recognizer = MagicMock()
                    mock_recognizer_class.return_value = mock_recognizer
                    mock_recognizer.recognize_google.return_value = "тестовый текст"

                    # Mock the AudioFile context manager
                    with patch('handlers.sr.AudioFile') as mock_audio_file:
                        mock_source = MagicMock()
                        mock_audio_file.return_value.__enter__.return_value = mock_source
                        mock_audio_file.return_value.__exit__.return_value = None

                        result = transcribe_audio_sync(temp_path)

                        assert result == "тестовый текст"
                        mock_audio.assert_called_once_with(temp_path)
                        mock_audio_instance.export.assert_called_once()
                        mock_recognizer.recognize_google.assert_called_once()

    finally:
        # Clean up
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@pytest.mark.asyncio
async def test_transcribe_audio():
    """Test async transcribe_audio wrapper"""
    with patch('handlers.transcribe_audio_sync') as mock_sync:
        mock_sync.return_value = "тестовый текст"

        result = await transcribe_audio("/some/file.ogg")

        assert result == "тестовый текст"
        mock_sync.assert_called_once_with("/some/file.ogg")