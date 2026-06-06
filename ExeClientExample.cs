using System.Net.Http.Json;
using System.Text.Json;

public sealed class ErpPurchaseAgentClient
{
    private readonly HttpClient _http;

    public ErpPurchaseAgentClient(string apiKey)
    {
        _http = new HttpClient
        {
            BaseAddress = new Uri("http://127.0.0.1:47831"),
            Timeout = TimeSpan.FromSeconds(60)
        };
        _http.DefaultRequestHeaders.Add("X-API-Key", apiKey);
    }

    public async Task<JsonDocument> ExtractPdfAsync(string pdfPath)
    {
        using var form = new MultipartFormDataContent();
        await using var stream = File.OpenRead(pdfPath);
        form.Add(new StreamContent(stream), "pdf", Path.GetFileName(pdfPath));
        using var response = await _http.PostAsync("/api/v1/extract", form);
        return await ReadResponseAsync(response);
    }

    public async Task<JsonDocument> PreviewPdfAsync(
        string pdfPath,
        string companyCode,
        string yearCode)
    {
        using var form = new MultipartFormDataContent();
        await using var stream = File.OpenRead(pdfPath);
        form.Add(new StreamContent(stream), "pdf", Path.GetFileName(pdfPath));
        form.Add(new StringContent(companyCode), "companycode");
        form.Add(new StringContent(yearCode), "yearcode");
        form.Add(new StringContent("true"), "strict_total");
        using var response = await _http.PostAsync("/api/v1/purchases/from-pdf/preview", form);
        return await ReadResponseAsync(response);
    }

    public async Task<JsonDocument> PreviewAsync(
        string companyCode,
        string yearCode,
        object normalizedInvoice)
    {
        using var response = await _http.PostAsJsonAsync(
            "/api/v1/purchases/preview",
            new
            {
                companycode = companyCode,
                yearcode = yearCode,
                strict_total = true,
                invoice = normalizedInvoice
            });
        return await ReadResponseAsync(response);
    }

    public async Task<JsonDocument> InsertAsync(string approvalToken)
    {
        using var response = await _http.PostAsJsonAsync(
            "/api/v1/purchases/insert",
            new { approval_token = approvalToken });
        return await ReadResponseAsync(response);
    }

    public async Task<JsonDocument> GetStatusAsync(string previewId)
    {
        using var response = await _http.GetAsync($"/api/v1/approvals/{previewId}");
        return await ReadResponseAsync(response);
    }

    public async Task<JsonDocument> SearchSuppliersAsync(string companyCode, string query)
    {
        var url = $"/api/v1/masters/suppliers?companycode={Uri.EscapeDataString(companyCode)}" +
                  $"&query={Uri.EscapeDataString(query)}";
        using var response = await _http.GetAsync(url);
        return await ReadResponseAsync(response);
    }

    public async Task<JsonDocument> SaveSupplierMappingAsync(
        string companyCode,
        string extractedName,
        string selectedErpName)
    {
        using var response = await _http.PostAsJsonAsync(
            "/api/v1/mappings/suppliers",
            new
            {
                companycode = companyCode,
                source_name = extractedName,
                target_name = selectedErpName
            });
        return await ReadResponseAsync(response);
    }

    public async Task<JsonDocument> SearchItemsAsync(string query)
    {
        using var response = await _http.GetAsync(
            $"/api/v1/masters/items?query={Uri.EscapeDataString(query)}");
        return await ReadResponseAsync(response);
    }

    public async Task<JsonDocument> SaveItemMappingAsync(
        string extractedName,
        string batch,
        string selectedItemCode)
    {
        using var response = await _http.PostAsJsonAsync(
            "/api/v1/mappings/items",
            new
            {
                source_name = extractedName,
                batch,
                item_code = selectedItemCode
            });
        return await ReadResponseAsync(response);
    }

    private static async Task<JsonDocument> ReadResponseAsync(HttpResponseMessage response)
    {
        var body = await response.Content.ReadAsStringAsync();
        if (!response.IsSuccessStatusCode)
            throw new InvalidOperationException(
                $"Agent returned {(int)response.StatusCode}: {body}");
        return JsonDocument.Parse(body);
    }
}
