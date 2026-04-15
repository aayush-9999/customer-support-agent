// frontend/src/App.jsx

import { useState, useEffect }         from "react";
import { useAuth }                      from "./hooks/useAuth";
import { useChat }                      from "./hooks/useChat";
import { AuthPage }                     from "./components/AuthPage";
import { ChatWindow }                   from "./components/ChatWindow";
import { ConversationSidebar }          from "./components/ConversationSidebar";
import "./app.css";

export default function App() {
  const { user, loading: authLoading, error: authError, login, register, logout, setError } = useAuth();
  const {
    messages, loading: chatLoading, sessionId,
    conversations, historyLoaded,
    send, newChat, loadConversation,
  } = useChat(user);

  // ── Theme ─────────────────────────────────────────────────────────────────
  // Initialise from localStorage so preference survives refresh.
  const [theme, setTheme] = useState(
    () => localStorage.getItem("leafy_theme") || "light"
  );

  // Apply theme to <html> element so CSS [data-theme="dark"] selectors work.
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("leafy_theme", theme);
  }, [theme]);

  const toggleTheme = () => setTheme((t) => (t === "light" ? "dark" : "light"));

  // ── Sidebar collapse ──────────────────────────────────────────────────────
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

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
        collapsed={sidebarCollapsed}
        onToggleCollapse={() => setSidebarCollapsed((c) => !c)}
        theme={theme}
        onToggleTheme={toggleTheme}
      />
      <main className="shell__main">
        <ChatWindow
          user={user}
          messages={messages}
          loading={chatLoading}
          onSend={send}
          sessionId={sessionId}
          theme={theme}
          onToggleTheme={toggleTheme}
        />
      </main>
    </div>
  );
}