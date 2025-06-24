import React, { useState } from 'react';
import axios from 'axios';

function parseBotResponse(response) {
  // Try to split the response into summary and terraform code
  const summaryMatch = response.match(/Summary:(.*?)(Terraform:|$)/s);
  const terraformMatch = response.match(/Terraform:\s*```hcl([\s\S]*?)```/);
  const summary = summaryMatch ? summaryMatch[1].trim() : null;
  const terraform = terraformMatch ? terraformMatch[1].trim() : null;
  return { summary, terraform };
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
    const { summary, terraform } = parseBotResponse(res.data.response);
    if (summary || terraform) {
      setMessages(msgs => [
        ...msgs,
        { sender: 'bot', summary, terraform, text: res.data.response, showApproval: true }
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
            {msg.sender === 'bot' && (msg.summary || msg.terraform) ? (
              <div>
                {msg.summary && <div style={{ margin: '8px 0', color: '#333' }}><b>Summary:</b> {msg.summary}</div>}
                {msg.terraform && (
                  <div style={{ margin: '8px 0' }}>
                    <b>Terraform:</b>
                    <pre style={{ background: '#f4f4f4', padding: 10, borderRadius: 4, overflowX: 'auto' }}>{msg.terraform}</pre>
                  </div>
                )}
                {msg.showApproval && i === messages.length - 1 && awaitingApproval && (
                  <div style={{ marginTop: 10 }}>
                    <button onClick={() => handleApproval('approve')} style={{ marginRight: 8, padding: '6px 16px', background: '#4caf50', color: 'white', border: 'none', borderRadius: 4 }}>Approve</button>
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