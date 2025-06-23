import React, { useState } from 'react';
import axios from 'axios';

function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');

  const sendMessage = async () => {
    if (!input.trim()) return;
    setMessages([...messages, { sender: 'user', text: input }]);
    const res = await axios.post('/chat', { message: input, user_id: 'demo' });
    setMessages(msgs => [...msgs, { sender: 'bot', text: res.data.response }]);
    setInput('');
  };

  return (
    <div style={{ maxWidth: 600, margin: '40px auto', fontFamily: 'sans-serif' }}>
      <h2>GCP Terraform Chatbot</h2>
      <div style={{ border: '1px solid #ccc', minHeight: 200, padding: 10, marginBottom: 10 }}>
        {messages.map((msg, i) => (
          <div key={i} style={{ textAlign: msg.sender === 'user' ? 'right' : 'left' }}>
            <b>{msg.sender === 'user' ? 'You' : 'Bot'}:</b> {msg.text}
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