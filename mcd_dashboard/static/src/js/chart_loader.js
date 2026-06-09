/** Chart.js loader for mcd_dashboard **/
// Ensure Chart.js is available in window.Chart
(function() {
    if (window.Chart) return; // already loaded
    
    // Define paths to try in order
    const paths = [
        '/web/static/lib/Chart/Chart.js',
        '/web/static/lib/chartjs/chart.umd.js',
        '/web/static/lib/chart.js/chart.umd.js',
    ];
    
    let currentIndex = 0;
    
    function loadNext() {
        if (currentIndex >= paths.length) {
            console.warn('[Chart Loader] All attempts to load Chart.js failed');
            return;
        }
        
        const path = paths[currentIndex];
        currentIndex++;
        
        const script = document.createElement('script');
        script.src = path;
        script.async = true;
        
        script.onload = function() {
            console.log('[Chart Loader] Loaded Chart.js from: ' + path);
            if (!window.Chart) {
                console.warn('[Chart Loader] Script loaded but Chart not in window, trying next...');
                loadNext();
            }
        };
        
        script.onerror = function() {
            console.warn('[Chart Loader] Failed to load from: ' + path + ', trying next...');
            loadNext();
        };
        
        document.head.appendChild(script);
    }
    
    // Start loading
    loadNext();
})();
