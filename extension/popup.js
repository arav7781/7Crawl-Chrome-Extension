document.addEventListener('DOMContentLoaded', () => {
    const scrapeToggle = document.getElementById('scrape-toggle');
    const processBtn = document.getElementById('process-btn');
    const languageSelect = document.getElementById('language');
    const statusDiv = document.getElementById('status');
    const linksDiv = document.getElementById('links');
    const summaryDiv = document.getElementById('summary');
    const translatedDiv = document.getElementById('translated');
    const graphImg = document.getElementById('graph-img');
    const chartCanvas = document.getElementById('word-freq-chart');
    let wordFreqChart = null;
  
    scrapeToggle.addEventListener('change', () => {
      processBtn.disabled = !scrapeToggle.checked;
      if (!scrapeToggle.checked) {
        statusDiv.textContent = '';
        statusDiv.classList.remove('show');
        linksDiv.innerHTML = '';
        linksDiv.classList.remove('show');
        summaryDiv.textContent = '';
        summaryDiv.classList.remove('show');
        translatedDiv.textContent = '';
        translatedDiv.classList.remove('show');
        graphImg.style.display = 'none';
        if (wordFreqChart) {
          wordFreqChart.destroy();
          wordFreqChart = null;
        }
      }
    });
  
    processBtn.addEventListener('click', async () => {
      statusDiv.textContent = 'Scraping website...';
      statusDiv.classList.add('show');
      const targetLanguage = languageSelect.value;
  
      chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
        const tabId = tabs[0].id;
        const url = tabs[0].url;
        try {
          const [{ result: scrapedData }] = await chrome.scripting.executeScript({
            target: { tabId },
            function: scrapeContent,
          });
  
          if (!scrapedData.content || !scrapedData.html) {
            statusDiv.textContent = 'No content scraped';
            statusDiv.classList.add('show');
            return;
          }
  
          statusDiv.textContent = 'Processing content...';
          statusDiv.classList.add('show');
  
          const response = await fetch('http://localhost:5000/process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              content: scrapedData.content,
              html: scrapedData.html,
              language: targetLanguage,
              url: url
            })
          });
  
          const data = await response.json();
          if (!response.ok) {
            statusDiv.textContent = `Error: ${data.error || 'Processing failed'}`;
            statusDiv.classList.add('show');
            return;
          }
  
          statusDiv.textContent = 'Processing complete!';
          statusDiv.classList.add('show');
  
          if (data.links && data.links.length > 0) {
            linksDiv.innerHTML = `Internal Links:<ul>${data.links.map(link => `<li><a href="${link}" target="_blank" class="link">${link}</a></li>`).join('')}</ul>`;
            linksDiv.classList.add('show');
          } else {
            linksDiv.textContent = 'No internal links found';
            linksDiv.classList.add('show');
          }
  
          summaryDiv.textContent = `Summary: ${data.summary || 'No summary available'}`;
          summaryDiv.classList.add('show');
  
          translatedDiv.textContent = `Translated (${targetLanguage}): ${data.translated || 'No translation available'}`;
          translatedDiv.classList.add('show');
  
          if (data.word_freq_data && Object.keys(data.word_freq_data).length > 0) {
            if (wordFreqChart) {
              wordFreqChart.destroy();
            }
            const words = Object.keys(data.word_freq_data);
            const freqs = Object.values(data.word_freq_data);
            wordFreqChart = new Chart(chartCanvas, {
              type: 'bar',
              data: {
                labels: words,
                datasets: [{
                  label: 'Word Frequency',
                  data: freqs,
                  backgroundColor: 'rgba(0, 255, 136, 0.7)',
                  borderColor: 'rgba(0, 255, 136, 1)',
                  borderWidth: 1
                }]
              },
              options: {
                responsive: true,
                plugins: {
                  legend: { display: false },
                  title: {
                    display: true,
                    text: 'Top 10 Word Frequencies',
                    color: '#fff'
                  }
                },
                scales: {
                  x: {
                    ticks: { color: '#fff', maxRotation: 45, minRotation: 45 },
                    grid: { display: false }
                  },
                  y: {
                    ticks: { color: '#fff' },
                    grid: { color: '#555' }
                  }
                }
              }
            });
          } else {
            chartCanvas.style.display = 'none';
          }
  
          if (data.graph_url) {
            graphImg.src = data.graph_url;
            graphImg.style.display = 'block';
          } else {
            graphImg.style.display = 'none';
          }
        } catch (error) {
          statusDiv.textContent = `Error: ${error.message}`;
          statusDiv.classList.add('show');
          console.error('Error:', error);
        }
      });
    });
  
    function scrapeContent() {
      const content = document.body.innerText || '';
      const html = document.documentElement.outerHTML || '';
      return {
        content: content.length > 0 ? content : null,
        html: html.length > 0 ? html : null
      };
    }
  });