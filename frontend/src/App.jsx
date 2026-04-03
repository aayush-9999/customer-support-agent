// frontend/src/App.jsx

import { useAuth }                 from "./hooks/useAuth";
import { useChat }                 from "./hooks/useChat";
import { AuthPage }                from "./components/AuthPage";
import { ChatWindow }              from "./components/ChatWindow";
import { ConversationSidebar }     from "./components/ConversationSidebar";
import "./app.css";

export default function App() {
  const { user, loading: authLoading, error: authError, login, register, logout, setError } = useAuth();
  const {
    messages, loading: chatLoading, sessionId,
    conversations, historyLoaded,
    send, newChat, loadConversation,
  } = useChat(user);

  const handleLogout = async () => {
    await logout(sessionId);
  };

  if (!user) {
    return (
      <AuthPage
        onLogin={login}
        onRegister={register}
        loading={authLoading}
        error={authError}
        onClearError={() => setError(null)}
      />
    );
  }

  return (
    <div className="shell">
      <ConversationSidebar
        user={user}
        conversations={conversations}
        historyLoaded={historyLoaded}
        onLoadConversation={loadConversation}
        onNewChat={newChat}
        onLogout={handleLogout}
        activeSessionId={sessionId}
      />
      <main className="shell__main">
        <ChatWindow
          user={user}
          messages={messages}
          loading={chatLoading}
          onSend={send}
          sessionId={sessionId}
        />
      </main>
    </div>
  );
}