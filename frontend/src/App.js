import React, { useState } from 'react';
import axios from 'axios';

function parseBotResponse(response) {
  // Extract summary
  let summary = null;
  let change = null;

  // Match summary between 'Summary:' and 'Change:'
  const summaryMatch = response.match(/Summary:(.*?)(Change:|$)/s);
  if (summaryMatch) {
    summary = summaryMatch[1].replace(/\n/g, ' ').trim();
  }

  // Match diff code block after 'Change:'
  const diffMatch = response.match(/Change:\s*```diff([\s\S]*?)```/);
  if (diffMatch) {
    change = diffMatch[1].trim();
  } else {
    // fallback: match any diff code block
    const fallbackDiff = response.match(/```diff([\s\S]*?)```/);
    if (fallbackDiff) {
      change = fallbackDiff[1].trim();
    }
  }

  return { summary, change };
}

function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [awaitingApproval, setAwaitingApproval] = useState(false);

  const user_id = 'demo';

  const sendMessage = async () => {
    if (!input.trim()) return;
    setMessages([...messages, { sender: 'user', text: input }]);
    const res = await axios.post('/chat', { message: input, user_id });
    const { summary, change } = parseBotResponse(res.data.response);
    if (summary || change) {
      setMessages(msgs => [
        ...msgs,
        { sender: 'bot', summary, change, text: res.data.response, showApproval: true }
      ]);
      setAwaitingApproval(true);
    } else {
      setMessages(msgs => [...msgs, { sender: 'bot', text: res.data.response }]);
    }
    setInput('');
  };

  const handleApproval = async (action) => {
    setAwaitingApproval(false);
    const res = await axios.post('/approve', { user_id, action });
    setMessages(msgs => [
      ...msgs,
      { sender: 'bot', text: res.data.result }
    ]);
  };

  return (
    <div style={{ maxWidth: 600, margin: '40px auto', fontFamily: 'sans-serif' }}>
      <h2>GCP Terraform Chatbot</h2>
      <div style={{ border: '1px solid #ccc', minHeight: 200, padding: 10, marginBottom: 10 }}>
        {messages.map((msg, i) => (
          <div key={i} style={{ textAlign: msg.sender === 'user' ? 'right' : 'left', marginBottom: 16 }}>
            <b>{msg.sender === 'user' ? 'You' : 'Bot'}:</b>
            {msg.sender === 'bot' && (msg.summary || msg.change) ? (
              <div>
                {msg.summary && <div style={{ margin: '8px 0', color: '#333' }}><b>Summary:</b> {msg.summary}</div>}
                {msg.change && (
                  <div style={{ margin: '8px 0' }}>
                    <b>Change:</b>
                    <pre style={{ background: '#f4f4f4', padding: 10, borderRadius: 4, overflowX: 'auto' }}>
                      {msg.change.split('\n').map((line, idx) => {
                        let color = '#222';
                        if (line.startsWith('+')) color = '#22863a'; // green
                        else if (line.startsWith('-')) color = '#cb2431'; // red
                        else if (line.startsWith('@@')) color = '#6f42c1'; // purple for hunk headers
                        return (
                          <span key={idx} style={{ color }}>
                            {line}
                            {'\n'}
                          </span>
                        );
                      })}
                    </pre>
                  </div>
                )}
                {msg.showApproval && i === messages.length - 1 && awaitingApproval && (
                  <div style={{ marginTop: 10 }}>
                    <button onClick={() => handleApproval('approve')} style={{ marginRight: 8, padding: '6px 16px', background: '#4caf50', color: 'white', border: 'none', borderRadius: 4 }}>Create New Branch</button>
                    <button onClick={() => handleApproval('reject')} style={{ padding: '6px 16px', background: '#f44336', color: 'white', border: 'none', borderRadius: 4 }}>Reject</button>
                  </div>
                )}
              </div>
            ) : (
              <span> {msg.text}</span>
            )}
          </div>
        ))}
      </div>
      <input
        value={input}
        onChange={e => setInput(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && sendMessage()}
        style={{ width: '80%', padding: 8 }}
        placeholder="Type your command..."
      />
      <button onClick={sendMessage} style={{ padding: 8, marginLeft: 8 }}>Send</button>
    </div>
  );
}

export default App; 