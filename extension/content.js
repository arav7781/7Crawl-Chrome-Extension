chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === 'scrape') {
      const content = document.body.innerText || '';
      sendResponse({ content: content.length > 0 ? content : null });
    }
  });